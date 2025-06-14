#pylint: disable=invalid-name, broad-except, missing-function-docstring, protected-access
#pylint: disable=broad-exception-raised
"""
lwfm Site driver for IBM Quantum

TODO Note: One side-effect of our venv approach is each call to a Site Pillar method is
stateless - in its own process, so the handle to the IBM cloud service is lost
at the end of each call. A workaround is to login at the top of each Pillar method, 
but a better approach would be to squirrel away the connection somewhere, like 
in a lwfm auth repo.
"""

from typing import List, Union
import io
import os
import urllib3

from qiskit import qpy

from qiskit.transpiler import generate_preset_pass_manager
from qiskit import QuantumCircuit
from qiskit.qasm3 import loads
from qiskit_ibm_runtime import QiskitRuntimeService
from qiskit_ibm_runtime import Sampler

from lwfm.base.JobContext import JobContext
from lwfm.base.JobStatus import JobStatus
from lwfm.base.JobDefn import JobDefn
from lwfm.base.Workflow import Workflow
from lwfm.base.Metasheet import Metasheet
from lwfm.base.Site import SiteAuth, SiteRun, SiteRepo, SiteSpin
from lwfm.midware.LwfManager import logger, lwfManager

# Suppress InsecureRequestWarning messages
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)





#**********************************************************************************
# Site Auth Driver

class IBMQuantumSiteAuth(SiteAuth):
    """
    A Site driver for managing the authentication for an IBM Quantum site
    """

    def _getToken(self) -> str:
        """
        Get the token for IBM Cloud which we store in ~/.lwfm/site.toml 
        """
        return lwfManager.getSiteProperties(self.getSiteName()).get("token")


    def _getIBMService(self) -> QiskitRuntimeService:
        """
        Get the QiskitRuntimeService. Not part of the general "Site" interface, this
        method is used by the other drivers to access the actual cloud service.
        """
        token = self._getToken()
        return QiskitRuntimeService(token=token,
                                    channel="ibm_cloud",
                                    verify=False)
                                    # might be needed for corp networks
                                    # verify=False)


    def login(self, force: bool = False) -> bool:
        """
        Login to the IBM Quantum site
        """
        logger.info("Attempting remote login to IBM Quantum site")
        try:
            self._getIBMService()
            logger.info("Successfully logged in to IBM Quantum site")
            return True
        except Exception as e:
            logger.error("Failed to login to IBM Quantum site: %s", e)
            return False


    def isAuthCurrent(self) -> bool:
        """
        Check if the authentication is current
        """
        if not self.login():
            logger.error("Unable to login to IBM cloud")
            return False


#**********************************************************************************
# internal module helper


def _getAuthDriver(siteName: str) -> IBMQuantumSiteAuth:
    """
    Return the auth driver for IBM Quantum Cloud. 
    TODO We -could- maybe use the site name to lookup into sites.toml, get the 
    actual IBM auth class name for this site, but its likely to be this one 
    anyway. Also, we assume the existance of the QiskitRuntimeSerivce, which 
    not be the case in an arbitrary auth driver.
    """
    siteAuth = IBMQuantumSiteAuth()
    siteAuth.setSiteName(siteName)
    return siteAuth


#**********************************************************************************
# Site Run Driver


class IBMQuantumSiteRun(SiteRun):
    """
    A Site driver for running jobs on IBM Quantum
    """

    def _mapStatus(self, ibmStatus: str) -> str:
        # Map IBM status strings to JobStatus enum values
        status_map = {
            "UNKNOWN"      : JobStatus.UNKNOWN,
            "INITIALIZING" : JobStatus.READY,
            "QUEUED"       : JobStatus.PENDING,
            "VALIDATING"   : JobStatus.PENDING,
            "RUNNING"      : JobStatus.RUNNING,
            "CANCELLED"    : JobStatus.CANCELLED,
            # nothing in IBM maps to lwfm FINISHING
            "DONE"         : JobStatus.COMPLETE,
            "ERROR"        : JobStatus.FAILED,
            "INFO"         : JobStatus.INFO
        }
        # Return the mapped status or UNKNOWN if not in map
        return status_map.get(ibmStatus, "UNKNOWN")


    def submit(self, jobDefn: Union['JobDefn', str],
        parentContext: Union[JobContext, Workflow, str] = None,
        computeType: str = None, runArgs: Union[dict, str] = None) -> JobStatus:
        """
        Run a quantum circuit on a quantum computer in the IBM cloud. 
        """

        try:
            # a "backend" is a quantum computer or simulator - we have no default
            if computeType is None:
                logger.error("computeType (backend) is None")
                return None

            # In lwfm, we can insulate the Site's dependencies with virtual environments.
            # But the cost is parameters to the site functions come in serialized.
            # If the argument is string, deserialize it, else its an object of the
            # expected kind.
            if isinstance(parentContext, str):
                parentContext = lwfManager.deserialize(parentContext)
            if isinstance(runArgs, str):
                runArgs = lwfManager.deserialize(runArgs)
            if isinstance(jobDefn, str):
                if runArgs is None or runArgs["format"] not in ["qasm3", "qpy", "python"]:
                    jobDefn = lwfManager.deserialize(jobDefn)

            # if we get no parent job context, make our own self one
            if parentContext is None:
                useContext = JobContext()
            elif isinstance(parentContext, JobContext):
                useContext = parentContext
            elif isinstance(parentContext, Workflow):
                useContext = JobContext()
                useContext.setWorkflowId(parentContext.getWorkflowId())
                useContext.setName(parentContext.getName())

            useContext.setSiteName(self.getSiteName())
            useContext.setComputeType(computeType)

            if jobDefn.getEntryPointType() == JobDefn.ENTRY_TYPE_SITE:
                return lwfManager.execSiteEndpoint(jobDefn, useContext, True)

            if jobDefn.getEntryPointType() != JobDefn.ENTRY_TYPE_STRING:
                logger.error("IBMQuantumSite.run.submit: unsupported entry point type")
                return None

            entry_point = jobDefn.getEntryPoint()
            if entry_point is None:
                logger.error("site submit entry point is None")
                return None

            # IBM Quantum Workflow:
            # 1. Map the problem to a quantum-native format.
            #    - we assume this was done by the user and we have a Qiskit circuit
            # 2. Optimize the circuits and operators.
            #    - we use the pass manager to optimize the circuit
            # 3. Execute using a quantum primitive function.
            #    - runtime parameters for the backend are passed in
            # 4. Analyze the results.
            #    - we use the runtime job to get the results asynchronously

            # get the IBM Quantum backend
            service: QiskitRuntimeService = \
                _getAuthDriver(self.getSiteName())._getIBMService()
            backend = service.backend(computeType)

            # the circuit may be expressed in a number of formats:
            # - in a qpy file (Qiskit)
            # - in a qasm3 file
            # - in a string in any of the above formats

            # ultimately we want to convert this format into a Qiskit QuantumCircuit
            # which we can then run through the transpilation pipeline
            qc = None
            if entry_point.endswith(".qpy"):
                if not os.path.exists(entry_point):
                    logger.error("entry point does not exist: " + entry_point)
                    return None
                # read the Qiskit circuit as QPY
                with open(entry_point, "rb") as file:
                    qpy_circuit = file.read()
                qc = qpy.load(io.BytesIO(qpy_circuit))
            elif entry_point.endswith(".qasm"):
                if not os.path.exists(entry_point):
                    logger.error("entry point does not exist: " + entry_point)
                    return None
                # read the Qiskit circuit as QASM
                with open(entry_point, "r", encoding="utf-8") as file:
                    qasm_circuit = file.read()
                qc = loads(qasm_circuit)
            elif runArgs is not None and runArgs["format"] == "qasm3":
                qc = loads(entry_point)
            elif runArgs is not None and runArgs["format"] == "qpy":
                qc = qpy.load(io.BytesIO(entry_point))
            elif runArgs is not None and runArgs["format"] == "qiskit":
                local_vars = {}
                exec(entry_point, globals(), local_vars)
                if 'qc' in local_vars:
                    qc = local_vars['qc']
            else:
                logger.error("unable to process entry point: " + entry_point)
                return None

            # 2. Optimize the circuits and operators.
            pm = generate_preset_pass_manager(backend=backend, optimization_level=1)
            isa_circuit = pm.run(qc)

            # 3. Execute using a quantum primitive function.
            sampler = Sampler(mode=backend)
            job = sampler.run([isa_circuit], shots=runArgs["shots"])
            logger.info("submitted ibm quantum job id: " + job.job_id())


            # there was no sense emitting a status until we knew the native job id,
            # so now, horse at the gate...
            useContext.setNativeId(job.job_id())
            lwfManager.emitStatus(useContext, self._mapStatus("QUEUED"), "QUEUED")

            # capture current job info & return it
            return self.getStatus(useContext.getJobId())
        except Exception as ex:
            logger.error("IBMQuantumSiteRun.submit error: " + str(ex))
            lwfManager.emitStatus(useContext, self._mapStatus("ERROR"), "ERROR", str(ex))
            return None


    def getStatus(self, jobId: str) -> JobStatus:
        """
        Get a job status from the IBM Quantum Cloud.
        """
        try:
            service: QiskitRuntimeService = \
                _getAuthDriver(self.getSiteName())._getIBMService()
            status = lwfManager.getStatus(jobId)
            if status is None:
                return None
            if status.isTerminal():
                return status
            job = service.job(
                status.getJobContext().getNativeId())
            if job is None:
                # return the latest status we have
                return status
            status = JobStatus(status.getJobContext())
            lwfmStatus = self._mapStatus(job.status())
            status.setStatus(lwfmStatus)
            status.setNativeStatus(job.status())
            if job.status() == "DONE":
                status.setNativeInfo(str(job.result()[0].data.meas.get_counts()))
            lwfManager.emitStatus(status.getJobContext(), lwfmStatus,
                                  job.status(), status.getNativeInfo())
            return status
        except Exception as e:
            logger.error("Failed to get status: %s", e)
            return None


    def cancel(self, jobContext: Union[JobContext, str]) -> bool:
        """
        Cancel a job in the IBM Quantum Cloud.
        """
        try:
            service: QiskitRuntimeService = \
                _getAuthDriver(self.getSiteName())._getIBMService()
            if isinstance(jobContext, str):
                jobContext = lwfManager.deserialize(jobContext)
            nativeId = jobContext.getNativeId()
            service.delete_job(nativeId)
            lwfManager.emitStatus(jobContext, self._mapStatus("CANCELLED"), "CANCELLED")
            return True
        except Exception as e:
            logger.error("Failed to cancel job: %s", e)
            return False


#**********************************************************************************
# Site Repo driver

class IBMQuantumSiteRepo(SiteRepo):
    """
    Repo driver.
    """

    def put(
        self,
        localPath: str,
        siteObjPath: str,
        jobContext: Union[JobContext, str] = None,
        metasheet: Union[Metasheet, str] = None
    ) -> Metasheet:
        raise Exception("Unsupported method 'repo.put'")


    # ask the site to fetch an object by reference and write it locally to a path,
    # returning the local path where written
    def get(self,
            siteObjPath: str,
            localPath: str,
            jobContext: Union[JobContext, str] = None) -> str:
        status = lwfManager.getStatus(siteObjPath)
        if status is None or not status.isTerminal():
            return None
        result = status.getNativeInfo()
        # TODO notate the metadata
        os.makedirs(os.path.dirname(os.path.abspath(localPath)), exist_ok=True)
        with open(localPath, 'w', encoding='utf-8') as file:
            file.write(result)
        return localPath



    # find metasheets by query
    def find(self, queryRegExs: Union[dict, str]) -> List[Metasheet]:
        raise Exception("Unsupported method 'repo.find'")





#**********************************************************************************
# Site Spin Driver


class IBMQuantumSiteSpin(SiteSpin):
    """
    A Site driver for managing the spin for an IBM Quantum site
    """

    def listComputeTypes(self) -> List[str]:
        """
        List the compute types available on the IBM Quantum site - these are 
        named quantum computers.
        """
        try:
            service: QiskitRuntimeService = \
                _getAuthDriver(self.getSiteName())._getIBMService()
            backends = service.backends()
            backend_names = [backend.name for backend in backends]
            return backend_names
        except Exception as e:
            logger.error("Failed to list compute types: %s", e)
            return []

#**********************************************************************************
