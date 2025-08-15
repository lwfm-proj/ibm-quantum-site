"""
IBM Quantum Site Test Circuit
"""

#pylint: disable=broad-exception-caught, protected-access

import os
import argparse
import sys

from qiskit.qasm3 import dumps
from qiskit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp

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


def parse_args():
    """
    command line switches
    """
    parser = argparse.ArgumentParser(description='Run quantum circuit on IBM Quantum')
    parser.add_argument('--compute-type', type=str,
                      help='The compute type to use')
    parser.add_argument('--list-types', action='store_true',
                      help='List all available compute types and exit')
    parser.add_argument('--get-results', type=str,
                      help='Job ID to fetch results for',
                      metavar='JOB_ID')
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    site = lwfManager.getSite("ibm-quantum-venv")

    if args.list_types:
        # get the list of available IBM machines, use the first one
        computeTypes = site.getSpinDriver().listComputeTypes()
        for compute_type in computeTypes:
            print(compute_type)
        sys.exit(0)

    if args.get_results:
        # Handle fetching results for a completed job
        job_id = args.get_results
        data_file = os.path.join(os.path.expanduser("~/.lwfm/out"), f"{job_id}.out")
        try:
            with open(data_file, 'r', encoding='utf-8') as f:
                results = f.read()
                print(f"\nResults from {data_file}:")
                print(results)
        except FileNotFoundError:
            print(f"Error: Results file not found for job {job_id}")
            print("The job might still be running or the job ID is incorrect.")
            sys.exit(1)
        except Exception as e:
            print(f"Error reading results file: {e}")
            sys.exit(1)
        sys.exit(0)

    computeType = args.compute_type
    if computeType is None or computeType == "":
        logger.error("computeType is None")
        sys.exit(1)
    logger.info(f"using compute type {computeType}")


    observable = lwfManager.serialize(SparsePauliOp.from_list([ ("IX", 1/2), ("ZI", -32) ]))

    # some runtime args for this run (shots, other runtime case params)
    runArgs={"shots": 1024,             # number of runs of the circuit
        "computeType": computeType,     # from the list of compute types for the site
        "estimator": True,              # use the estimator primitive
        "observable": observable,       # with this observable
        "optimization_level": 3}        # agressive transpiler optimization (values: 0-3)

    # define a workflow context and capture some meta info about it up front - we can
    # append more as the workflow progresses, and individual jobs & data elements will
    # also have their own cross-referenced metadata
    wf = Workflow()
    wf.setName("IBM simple Hadamard")
    wf.setDescription("A simple quantum test to exercise runtime variations")
    wf.setProps(runArgs) # set any workflow metatadata properties, if desired
    lwfManager.putWorkflow(wf)

    # create job definition for the circuit & submit it to the IBM site
    jobDefnA = JobDefn(get_qasm_circuit(get_quantum_circuit()), JobDefn.ENTRY_TYPE_STRING,
        {"format": "qasm3"})
    statusA = site.getRunDriver().submit(jobDefnA, wf, computeType, runArgs)
    logger.info(f"Submitted job A: {statusA}")

    # make an output filename for this run
    dataFile = os.path.join(os.path.expanduser("~/.lwfm/out"), statusA.getJobId() + ".out")

    # when job A asynchronously reaches the COMPLETE state, fire job B to fetch results
    # and write it to the data file for the run
    statusB = lwfManager.setEvent(
        JobEvent(statusA.getJobId(), JobStatus.COMPLETE,
                 JobDefn("repo.get", JobDefn.ENTRY_TYPE_SITE,
                        [statusA.getJobId(),  # Use the job ID as the source path
                         dataFile]),          # Local destination path
                 "ibm-quantum-venv", None,    # Use the IBM quantum site
                 statusA.getJobContext())
    )
    if statusB is None:
        logger.error("Failed to create job B")
        sys.exit(1)

    if computeType.endswith("_aer"):
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
    else:
        print(f"Submitted cloud job ID: {statusA.getJobId()}")
        print("To retrieve results later, run:")
        print(f"python {sys.argv[0]} --get-results {statusA.getJobId()}")
