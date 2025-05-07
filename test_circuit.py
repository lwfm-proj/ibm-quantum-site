"""
IBM Quantum Site Test Circuit
"""
from typing import List

from qiskit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp

def get_quantum_circuit() -> QuantumCircuit:
    # Create a new circuit with two qubits
    qc = QuantumCircuit(2)
    # Add a Hadamard gate to qubit 0
    qc.h(0)
    # Perform a controlled-X gate on qubit 1, controlled by qubit 0
    qc.cx(0, 1)
    return qc

def get_observables() -> List[SparsePauliOp]:
    # Set up six different observables.
    observables_labels = ["IZ", "IX", "ZI", "XI", "ZZ", "XX"]
    observables = [SparsePauliOp(label) for label in observables_labels]
    return observables



# Construct the Estimator instance.
# estimator = Estimator(mode=backend)
# estimator.options.resilience_level = 1
# estimator.options.default_shots = 5000

# mapped_observables = [
#     observable.apply_layout(isa_circuit.layout) for observable in observables
# ]

# # One pub, with one circuit to run against five different observables.
# job = estimator.run([(isa_circuit, mapped_observables)])
