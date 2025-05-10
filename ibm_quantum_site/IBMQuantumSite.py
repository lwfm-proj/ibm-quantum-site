#pylint: disable=invalid-name
"""
lwfm Site driver for IBM Quantum
"""

from typing import List, Union
from enum import Enum
import io
import os

from qiskit_ibm_runtime import QiskitRuntimeService
from qiskit_ibm_runtime import Sampler
from qiskit.transpiler import generate_preset_pass_manager
from qiskit import qpy
from qiskit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp

from lwfm.base.JobContext import JobContext
from lwfm.base.JobStatus import JobStatus, JobStatusValues
from lwfm.base.JobDefn import JobDefn
from lwfm.base.Workflow import Workflow
from lwfm.base.Site import Site, SiteAuth, SiteRun, SiteRepo, SiteSpin
from lwfm.sites.LocalSite import LocalSiteRepo
from lwfm.midware.LwfManager import logger, lwfManager

#pylint: disable=broad-except


SITE_NAME = "ibm-quantum"

#**********************************************************************************

class IBMQuantumJobStatusValues(Enum):
    """
    Job status values for IBM Quantum
    """
    UNKNOWN = "UNKNOWN"
    INITIALIZING = "INITIALIZING"     # READY
    QUEUED = "QUEUED"                 # PENDING
    VALIDATING = "VALIDATING"         # PENDING
    RUNNING = "RUNNING"               # RUNNING
    INFO = "INFO"                     # INFO
    DONE = "DONE"                     # COMPLETE
    ERROR = "ERROR"                   # FAILED
    CANCELLED = "CANCELLED"           # CANCELLED


class IBMQuantumJobStatus(JobStatus):
    """
    Job status for IBM Quantum
    """
    def __init__(self, jobContext: JobContext = None):
        super(IBMQuantumJobStatus, self).__init__(jobContext)
        # override the default status mapping for the specifics of this site
        self.setStatusMap({
            IBMQuantumJobStatusValues.UNKNOWN.value       : JobStatusValues.UNKNOWN  ,
            IBMQuantumJobStatusValues.INITIALIZING.value  : JobStatusValues.READY    ,
            IBMQuantumJobStatusValues.QUEUED.value        : JobStatusValues.PENDING  ,
            IBMQuantumJobStatusValues.VALIDATING.value    : JobStatusValues.PENDING  ,
            IBMQuantumJobStatusValues.RUNNING.value       : JobStatusValues.RUNNING  ,
            IBMQuantumJobStatusValues.CANCELLED.value     : JobStatusValues.CANCELLED,
            # nothing in IBM maps to lwfm FINISHING
            IBMQuantumJobStatusValues.DONE.value          : JobStatusValues.COMPLETE ,
            IBMQuantumJobStatusValues.ERROR.value         : JobStatusValues.FAILED   ,
            IBMQuantumJobStatusValues.INFO.value          : JobStatusValues.INFO     ,
            })
        self.getJobContext().setSiteName(SITE_NAME)


#**********************************************************************************
# Site Auth Driver

class IBMQuantumSiteAuth(SiteAuth):
    """
    A Site driver for managing the authentication for an IBM Quantum site
    """

    def __init__(self):
        super().__init__()
        self._service = None

    def getService(self) -> QiskitRuntimeService:
        """
        Get the QiskitRuntimeService
        """
        return self._service


    def getToken(self) -> str:
        """
        Get the token
        """
        return Site.getSiteProperties(SITE_NAME).get("token")


    def login(self, force: bool = False) -> bool:
        """
        Login to the IBM Quantum site
        """
        logger.info("Logging in to IBM Quantum site")
        try:
            self._service.active_account()
            return True
        except Exception:
            pass
        try:
            token = self.getToken()
            self._service = QiskitRuntimeService(token=token,
                                                channel="ibm_cloud",
                                                verify=False)
                                                # might be needed for corp networks
                                                # verify=False)
            logger.info("Logged in to IBM Quantum site")
            return True
        except Exception as e:
            logger.error("Failed to login to IBM Quantum site: %s", e)
            return False

    def isAuthCurrent(self) -> bool:
        """
        Check if the authentication is current
        """
        logger.info("Checking if authentication is current")
        try:
            self._service.active_account()
            return True
        except Exception as e:
            logger.error("Authentication is not current: %s", e)
            return False

#**********************************************************************************
# Site Run Driver


class IBMQuantumSiteRun(SiteRun):
    """
    A Site driver for running jobs on IBM Quantum
    """
    def __init__(self, auth: IBMQuantumSiteAuth = None):
        super().__init__()
        self._auth = auth

    def submit(self, jobDefn: Union['JobDefn', str],
        parentContext: Union[JobContext, Workflow, str] = None,
        computeType: str = None, runArgs: Union[dict, str] = None) -> JobStatus:

        try:
            if computeType is None:
                logger.error("computeType (backend) is None")
                return None
            if isinstance(jobDefn, str):
                jobDefn = lwfManager.deserialize(jobDefn)
            if isinstance(parentContext, str):
                parentContext = lwfManager.deserialize(parentContext)
            if isinstance(runArgs, str):
                runArgs = lwfManager.deserialize(runArgs)

            if parentContext is None:
                useContext = JobContext()
            elif isinstance(parentContext, JobContext):
                useContext = parentContext
            elif isinstance(parentContext, Workflow):
                useContext = JobContext()
                useContext.setWorkflowId(parentContext.getWorkflowId())
                useContext.setName(parentContext.getName())

            useContext.setSiteName(SITE_NAME)
            useContext.setComputeType(computeType)

            script_path = jobDefn.getEntryPoint()
            if script_path is None:
                logger.error("entry point is None")
                return None
            if not os.path.exists(script_path):
                logger.error("entry point does not exist: " + script_path)
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
            service = self._auth.getService()
            backend = service.backend(computeType)
            if parentContext is None:
                # assert readiness
                lwfManager.emitStatus(useContext, IBMQuantumJobStatus,
                    IBMQuantumJobStatusValues.INITIALIZING.value)

            qpy_circuit = None
            if script_path.endswith(".py"):
                # read the Qiskit circuit as Python
                with open(script_path, "r", encoding="utf-8") as file:
                    buffer = io.BytesIO()
                    qpy.dump(file, buffer)
                    qpy_circuit = buffer.getvalue()
            elif script_path.endswith(".qpy"):
                # read the Qiskit circuit as QPY
                with open(script_path, "rb") as file:
                    qpy_circuit = file.read()
            else:
                # consider it a stringified QPY circuit
                qpy_circuit = script_path.encode("utf-8")

            # 2. Optimize the circuits and operators.
            pm = generate_preset_pass_manager(backend=backend, optimization_level=1)
            isa_circuit = pm.run(qpy.load(io.BytesIO(qpy_circuit)))

            # 3. Execute using a quantum primitive function.
            sampler = Sampler(mode=backend)
            job = sampler.run([isa_circuit], shots=runArgs["shots"])
            logger.info("submitted ibm quantum job id: " + job.job_id())
            useContext.setNativeId(job.job_id())

            # horse at the gate...
            lwfManager.emitStatus(useContext, IBMQuantumJobStatus,
                                IBMQuantumJobStatusValues.QUEUED.value)

            # capture current job info & return it
            return lwfManager.getStatus(useContext.getId())
        except Exception as ex:
            logger.error("IBMQuantumSiteRun.submit error: " + str(ex))
            lwfManager.emitStatus(useContext, IBMQuantumJobStatus,
                IBMQuantumJobStatusValues.ERROR.value, str(ex))
            return None


    def getStatus(self, jobId: str) -> JobStatus:
        try:
            status = lwfManager.getStatus(jobId)
            if status is None:
                return None
            if status.isTerminal():
                return status
            job = self._auth.getService().job(status.getJobContext().getNativeId())
            if job is None:
                # return the latest status we have
                return status
            status = IBMQuantumJobStatus(status.getJobContext())
            status.setNativeStatus(job.status())
            if job.status() == "DONE":
                status.setNativeInfo(str(job.result()[0].data.meas.get_counts()))
            lwfManager.emitStatus(status.getJobContext(), IBMQuantumJobStatus,
                                  job.status(), status.getNativeInfo())
            return status
        except Exception as e:
            logger.error("Failed to get status: %s", e)
            return None


    def cancel(self, jobContext: Union[JobContext, str]) -> bool:
        try:
            if isinstance(jobContext, str):
                jobContext = lwfManager.deserialize(jobContext)
            nativeId = jobContext.getNativeId()
            self._auth.getService().delete_job(nativeId)
            lwfManager.emitStatus(jobContext, JobStatusValues.CANCELLED.value, "")
            return True
        except Exception as e:
            logger.error("Failed to cancel job: %s", e)
            return False


#**********************************************************************************
# there is no repo driver for IBM Quantum - use LocalSiteRepo


#**********************************************************************************
# Site Spin Driver


class IBMQuantumSiteSpin(SiteSpin):
    """
    A Site driver for managing the spin for an IBM Quantum site
    """

    def __init__(self, auth: IBMQuantumSiteAuth = None):
        super().__init__()
        self._auth = auth

    def listComputeTypes(self) -> List[str]:
        """
        List the compute types available on the IBM Quantum site
        """
        try:
            backends = self._auth.getService().backends()
            backend_names = [backend.name for backend in backends]
            return backend_names
        except Exception as e:
            logger.error("Failed to list compute types: %s", e)
            return []


#**********************************************************************************
# Site Driver

class IBMQuantumSite(Site):
    """
    A Site driver for IBM Quantum
    """


    def __init__(self, site_name: str = None,
                 auth_driver: SiteAuth = None,
                 run_driver: SiteRun = None,
                 repo_driver: SiteRepo = None,
                 spin_driver: SiteSpin = None):
        ibmSiteAuth = IBMQuantumSiteAuth()
        super().__init__(
            site_name or SITE_NAME,
            auth_driver or ibmSiteAuth,
            run_driver  or IBMQuantumSiteRun(ibmSiteAuth),
            repo_driver or LocalSiteRepo(),
            spin_driver or IBMQuantumSiteSpin(ibmSiteAuth)
        )


#**********************************************************************************
# Main - testing only

def get_quantum_circuit() -> QuantumCircuit:
    # Create a new circuit with two qubits
    qc = QuantumCircuit(2)
    # Add a Hadamard gate to qubit 0
    qc.h(0)
    # Perform a controlled-X gate on qubit 1, controlled by qubit 0
    qc.cx(0, 1)
    qc.measure_all()
    return qc

def get_observables() -> List[SparsePauliOp]:
    # Set up six different observables.
    observables_labels = ["IZ", "IX", "ZI", "XI", "ZZ", "XX"]
    observables = [SparsePauliOp(label) for label in observables_labels]
    return observables


def main():
    """
    Main function for testing the IBM Quantum site driver
    """
    site = Site.getSite(SITE_NAME)
    site.getAuthDriver().login()
    print("Auth current: ", site.getAuthDriver().isAuthCurrent())
    print("Compute types: ", site.getSpinDriver().listComputeTypes())

    #jobDefn = JobDefn(qpy.dump(get_quantum_circuit()))
    #jobStatus = site.getRunDriver().submit(jobDefn, None, "ibm_brisbane", None)
    #print("Job status: ", jobStatus)


if __name__ == "__main__":
    main()
