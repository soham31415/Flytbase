"""The engine: computes the five answers per account from the store.

Planned modules (Step 3b): q1_book, q2_power, q3_moves, q4_risks, q5_gaps,
validate (drops any item whose sources are empty or not in id_index) and run
(compute(as_of) -> {as_of, accounts: {id: {q1..q5}}, dropped}). Thresholds and
weights come from config/rules.yaml. The engine never calls the MCP.
"""
