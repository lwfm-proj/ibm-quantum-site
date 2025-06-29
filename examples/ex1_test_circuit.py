"""
IBM Quantum Site Test Circuit
"""

#pylint: disable=broad-exception-caught

import sys
import os

from qiskit.qasm3 import dumps
from qiskit import QuantumCircuit

from lwfm.base.JobDefn import JobDefn
from lwfm.base.JobStatus import JobStatus
from lwfm.base.WorkflowEvent import JobEvent
from lwfm.base.Workflow import Workflow
from lwfm.midware.LwfManager import lwfManager, logger



def get_quantum_circuit() -> QuantumCircuit:
    """
    Create a new circuit with two qubits
    """
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure_all()
    return qc

def get_qasm_circuit(qc: QuantumCircuit) -> str:
    """
    Convert a Qiskit circuit to QASM
    """
    return dumps(qc)


if __name__ == "__main__":
    site = lwfManager.getSite("ibm-quantum-venv")

    # login to the IBM site
    site.getAuthDriver().login()

    # get the list of available IBM machines, use the first one
    computeTypes = site.getSpinDriver().listComputeTypes()
    computeType = computeTypes[0]
    logger.info(f"computeTypes: {computeTypes}")
    logger.info(f"using: {computeType}")

    # some runtime args for this run (shots, other runtime case params)
    runArgs={"shots": 1024, "computeType": computeType}


    # define an [optional] workflow context to capture some meta info about it up front
    wf = Workflow()
    wf.setName("IBM simple Hadamard")
    wf.setDescription("A simple quantum test to exercise runtime variations")
    wf.setProps(runArgs) # set any workflow metatadata properties, if desired
    lwfManager.putWorkflow(wf)

    # create job definition & submit it to the IBM site
    jobDefnA = JobDefn(get_qasm_circuit(get_quantum_circuit()), JobDefn.ENTRY_TYPE_STRING,
        {"format": "qasm3"})
    statusA = site.getRunDriver().submit(jobDefnA, wf, computeType, runArgs)
    logger.info(f"Submitted job A: {statusA}")

    # make an output filename for this run
    dataFile = os.path.join(os.path.expanduser("~/.lwfm/out"),
        "quantum_results_" + statusA.getJobId() + ".out")

    # when job A asynchronously reaches the COMPLETE state, fire job B to fetch results
    # and write it to the file identified by the job id
    statusB = lwfManager.setEvent(
        JobEvent(statusA.getJobId(), JobStatus.COMPLETE,
                 JobDefn("repo.get", JobDefn.ENTRY_TYPE_SITE,
                        [statusA.getJobId(),  # Use the job ID as the source path
                         dataFile]),          # Local destination path
                 "ibm-quantum-venv", None,    # Use the IBM quantum site
                 statusA.getJobContext())
    )

    # for the purposes of this example, let's wait synchronously
    statusB = lwfManager.wait(statusB.getJobId())

    # Display the contents of the results file
    expanded_path = os.path.expanduser(dataFile)
    print(f"\nResults from {expanded_path}:")
    try:
        with open(expanded_path, 'r', encoding='utf-8') as f:
            results = f.read()
            print(results)
    except Exception as e:
        print(f"Error reading results file: {e}")
