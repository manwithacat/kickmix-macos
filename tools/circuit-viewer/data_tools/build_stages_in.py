#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
# AI-assisted (Claude, Anthropic), under the repository owner's direction.
"""Stage and qubit-group skeleton (stages_in.json) for the 46-qubit invsqrt circuit 321aff836362, from a cuts file
(name/start/end/note/approximate per stage). build_live.js then fills in the Toffoli counts and writes stages.json.

Usage: python build_stages_in.py cuts.json out.json
Stages that are exact builder cuts are tagged "documented"; the four divider sub-stages, whose boundaries were
placed from the op stream and are uncertain by tens to hundreds of ops, are tagged "inferred".
"""
import json, sys

TITLES = {
    'NORM': 'Normalise the input', 'ROOT': 'Consuming square root', 'LOAD1': 'First table load',
    'COEF1': 'First coefficient arithmetic', 'META1': 'First metadata retired', 'SEED': 'Seed ready',
    'PROD': 'Signed 15-bit product', 'CRET': 'Coefficient retired', 'DIV_FWD': 'Divider, forward rows',
    'PHASE': 'Phase endpoint', 'DIV_EXIT': 'Divider exit', 'DIV_CLEAN': 'Divider clean-up',
    'LOAD2': 'Second table load', 'H3': 'Second H3 ready', 'COEF2': 'Second coefficient arithmetic',
    'META2': 'Second metadata retired', 'SEED_CLR': 'Seed cleared', 'CSRC': 'Coefficient sources restored',
    'A_REST': 'A restored', 'R_PAID': 'Raw root restored, R repaid', 'BODY_RET': 'Body return',
    'ROOT_INV': 'Square root undone', 'NORM_INV': 'Normalisation undone', 'LAYOUT': 'Final layout swaps'}

cuts = json.load(open(sys.argv[1]))
stages = []
for s in cuts['stages']:
    approx = s['approximate']
    desc = s['note'][0].upper() + s['note'][1:] + '.'
    desc += (' The boundaries of this divider sub-stage are approximate (placed from the op stream, not read from the generator): '
             'treat the start and end as good to tens to a few hundred ops.') if approx else \
            ' Boundary read from the circuit generator\'s own cut list (Syed Asad Ali\'s builder).'
    stages.append({'id': s['name'].lower(), 'start': s['start'], 'end': s['end'], 'title': TITLES[s['name']] + (' (approx.)' if approx else ''),
                   'desc': desc, 'tof': 0, 'tof_mean': 0, 'ev': ['inferred'] if approx else ['documented'], 'n_zpow': 0, 'n_hmr': 0})
assert stages[0]['start'] == 0 and all(a['end'] == b['start'] for a, b in zip(stages, stages[1:]))
groups = [
    {'name': 'x_reg', 'qubits': list(range(22)), 'role': 'input register "x" (little endian): holds x at the start, is rewritten in place by the normalisation and the square root, and is restored at the end', 'evidence': 'verifier contract + register declaration in the file'},
    {'name': 'scratch', 'qubits': list(range(22, 46)), 'role': 'all 24 non-input wires. The file does not name them and the builder allocates them at first touch, so no finer register groups are claimed for this circuit (see the stage list for what runs when)', 'evidence': 'verifier contract; no per-wire roles assigned'}]
json.dump({'circuit': "321aff836362 (Syed Asad Ali, working with a team of AI agents; 46 qubits)", 'stages': stages, 'groups': groups,
           'totals': {}}, open(sys.argv[2], 'w'), separators=(',', ':'))
print(len(stages), 'stages', stages[-1]['end'], 'ops')
