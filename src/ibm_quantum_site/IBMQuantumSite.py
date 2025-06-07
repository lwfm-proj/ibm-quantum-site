#pylint: disable=invalid-name, broad-except, missing-function-docstring
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
from qiskit import QuantumCircuit

from qiskit.transpiler import generate_preset_pass_manager

from qiskit.quantum_info import SparsePauliOp
from qiskit.qasm3 import dumps, loads
from qiskit_ibm_runtime import QiskitRuntimeService
from qiskit_ibm_runtime import Sampler

from lwfm.base.JobContext import JobContext
from lwfm.base.JobStatus import JobStatus
from lwfm.base.JobDefn import JobDefn
from lwfm.base.Workflow import Workflow
from lwfm.base.Site import Site, SiteAuth, SiteRun, SiteRepo, SiteSpin
from lwfm.sites.LocalSite import LocalSiteRepo
from lwfm.midware.LwfManager import logger, lwfManager

# Suppress InsecureRequestWarning messages
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


#**********************************************************************************
# Site Auth Driver

class IBMQuantumSiteAuth(SiteAuth):
    """
    A Site driver for managing the authentication for an IBM Quantum site
    """

    def __init__(self):
        super().__init__()
        self._service = None

    def getIBMService(self) -> QiskitRuntimeService:
        """
        Get the QiskitRuntimeService. Not part of the general "Site" interface, this
        method is used by the other drivers to access the actual cloud service.
        """
        return self._service


    def _getToken(self) -> str:
        """
        Get the token for IBM Cloud which we store in ~/.lwfm/site.toml 
        """
        return lwfManager.getSiteProperties(self.getSiteName()).get("token")


    def login(self, force: bool = False) -> bool:
        """
        Login to the IBM Quantum site
        """
        logger.info("Attempting remote login to IBM Quantum site")
        try:
            self._service.active_account()
            return True
        except Exception:
            pass
        try:
            token = self._getToken()
            self._service = QiskitRuntimeService(token=token,
                                                channel="ibm_cloud",
                                                verify=False)
                                                # might be needed for corp networks
                                                # verify=False)
            logger.info("Successfully logged in to IBM Quantum site")
            return True
        except Exception as e:
            logger.error("Failed to login to IBM Quantum site: %s", e)
            return False

    def isAuthCurrent(self) -> bool:
        """
        Check if the authentication is current
        """
        if not self.getSite().getAuthDriver().login():
            logger.error("Unable to login to IBM cloud")
            return False


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
            if not self.getSite().getAuthDriver().login():
                logger.error("Unable to login to IBM cloud")
                return None

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

            useContext.setSiteName(self.getSite().getSiteName())
            useContext.setComputeType(computeType)

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
            service: QiskitRuntimeService = self.getSite().getAuthDriver().getIBMService()
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
            else:
                logger.error("unable to process entry point: " + entry_point)
                return None

            if parentContext is None:
                # assert readiness
                lwfManager.emitStatus(useContext, self._mapStatus("INITIALIZING"), "INITIALIZING")

            # 2. Optimize the circuits and operators.
            pm = generate_preset_pass_manager(backend=backend, optimization_level=1)
            isa_circuit = pm.run(qc)

            # 3. Execute using a quantum primitive function.
            sampler = Sampler(mode=backend)
            job = sampler.run([isa_circuit], shots=runArgs["shots"])
            logger.info("submitted ibm quantum job id: " + job.job_id())
            useContext.setNativeId(job.job_id())

            # horse at the gate...
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
            if not self.getSite().getAuthDriver().login():
                logger.error("Unable to login to IBM cloud")
                return None
            status = lwfManager.getStatus(jobId)
            if status is None:
                return None
            if status.isTerminal():
                return status
            job = self.getSite().getAuthDriver().getIBMService().job(
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
            if not self.getSite().getAuthDriver().login():
                logger.error("Unable to login to IBM cloud")
                return None
            if isinstance(jobContext, str):
                jobContext = lwfManager.deserialize(jobContext)
            nativeId = jobContext.getNativeId()
            self.getSite().getAuthDriver().getIBMService().delete_job(nativeId)
            lwfManager.emitStatus(jobContext, self._mapStatus("CANCELLED"), "CANCELLED")
            return True
        except Exception as e:
            logger.error("Failed to cancel job: %s", e)
            return False


#**********************************************************************************
# there is no repo driver for IBM Quantum - using LocalSiteRepo


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
            if not self.getSite().getAuthDriver().login():
                logger.error("Unable to login to IBM cloud")
                return None
            backends = self.getSite().getAuthDriver().getIBMService().backends()
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

    SITE_NAME = "ibm-quantum"


    def __init__(self, site_name: str = None,
                 auth_driver: SiteAuth = None,
                 run_driver: SiteRun = None,
                 repo_driver: SiteRepo = None,
                 spin_driver: SiteSpin = None):
        self._authDriver = auth_driver or IBMQuantumSiteAuth()
        self._runDriver = run_driver or IBMQuantumSiteRun()
        self._repoDriver = repo_driver or LocalSiteRepo()
        self._spinDriver = spin_driver or IBMQuantumSiteSpin()
        self._authDriver.setSite(self)
        self._runDriver.setSite(self)
        self._repoDriver.setSite(self)
        self._spinDriver.setSite(self)
        super().__init__(site_name,
            self._authDriver,
            self._runDriver,
            self._repoDriver,
            self._spinDriver)


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


def poll_job(jobid : str, siteName: str = IBMQuantumSite.SITE_NAME):
    site = lwfManager.getSite(siteName)
    site.getAuthDriver().login()
    job_status = site.getRunDriver().getStatus(jobid)
    print(f"*** lwfm job {job_status.getJobId()} " + \
        f"is IBM job {job_status.getJobContext().getNativeId()} " + \
        f"status: {job_status.getStatus()}")
    if job_status.getStatus() == "COMPLETE":
        print(f"native info: {job_status.getNativeInfo()}")


def main(siteName : str = IBMQuantumSite.SITE_NAME):
    """
    Main function for testing the IBM Quantum site driver
    """
    site = lwfManager.getSite(siteName)
    site.getAuthDriver().login()
    print(f"*** Running as site {site.getSiteName()}")
    isLoggedIn = site.getAuthDriver().isAuthCurrent()
    print("*** Auth current: ", isLoggedIn)
    if not isLoggedIn:
        print("IBM authentication failed - exiting")

    computeTypes = site.getSpinDriver().listComputeTypes()
    print("*** Compute types: ", computeTypes)
    if computeTypes is None:
        computeTypes = []

    if "ibm_brisbane" in computeTypes:
        # Get the quantum circuit
        circuit = get_quantum_circuit()
        print(f"*** Circuit has {circuit.qubits}")

        # Serialize the circuit to OpenQASM format (could also do QPY)
        circuit_str = dumps(circuit)

        # Create a JobDefn with the serialized circuit
        # The JobDefn constructor accepts a string representation of the job definition
        job_defn = JobDefn(circuit_str)

        # Submit the job
        print("*** Ready to send circuit to IBM")
        job_status = site.getRunDriver().submit(job_defn, None, "ibm_brisbane",
            {"shots": 1024, "format": "qasm3"})
        print(f"*** lwfm job {job_status.getJobId()} " + \
            f"is IBM job {job_status.getJobContext().getNativeId()} " + \
            f"initial status: {job_status.getStatus()}")

        # we don't want to wait synchronously - this IBM Cloud job might sit in queue
        # for a while though the runtime is short
        # but let's poll for status one time to show we can
        job_status = site.getRunDriver().getStatus(job_status.getJobId())
        print(f"*** lwfm job {job_status.getJobId()} " + \
            f"is IBM job {job_status.getJobContext().getNativeId()} " + \
            f"status: {job_status.getStatus()}")
    else:
        print("*** No ibm_brisbane anymore... RIP... we'll need to pick another one.")


if __name__ == "__main__":
    import sys

    # Check if an argument was provided (job ID)
    if len(sys.argv) > 1:
        job_id = sys.argv[1]
        print(f"Job ID provided: {job_id}")
        # Handle job ID here
        poll_job(job_id)
    else:
        # No arguments, run the main function
        main()
