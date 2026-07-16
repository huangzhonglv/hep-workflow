# Electroweak Minimal Examples

This file collects **project-local additions** of minimal electroweak examples.
They are not entries matched against `tutorial.pdf`; they strengthen the basic
electroweak structures whose formulas are already specified in
`standard-theories.md` but previously lacked end-to-end validation examples.

---

## Contents

§EW1. `W^- -> e^- \bar\nu_e` (tree diagram, minimal left-handed-current check)
§EW2. `e^+ e^- -> \mu^+ \mu^-` (tree diagram, minimal `A-Z` interference check)

---

## §EW1: `W^- -> e^- \bar\nu_e` (tree diagram, minimal left-handed-current check)

### User Input (simulated)

> Give me a minimal electroweak tree-level example that checks the Package-X
> syntax for `\gamma^\mu P_L` in the `Wff'` vertex and the W polarization sum.

### Physics Analysis

- **Theory framework:** Standard Model electroweak sector
- **Calculation target:** Check the Dirac/Lorentz part of the `Wff'`
  left-handed current in the massless-final-state limit
- **Process:** `W^- -> e^- \bar\nu_e`
- **Vertex structure:** Write `\gamma^\mu P_L` as
  `LTensor[\[Gamma], \[Mu]], \[DoubleStruckCapitalP]L`
- **Kinematics:** `k = p1 + p2`, `p1^2 = p2^2 = 0`, `k^2 = MW^2`
- **Key checks:**
  1. The longitudinal polarization term `k^\mu k^\nu / MW^2` gives `0` in the
     massless-final-state limit
  2. The total Dirac/Lorentz part simplifies to `2 MW^2`

### Code

```mathematica
(* ============================================================ *)
(* Dirac/Lorentz part of W^- -> e^- nubar_e at tree level       *)
(* Project-local minimal electroweak validation example          *)
(* ============================================================ *)

<< X`

onShell = {
  LDot[k, k] -> MW^2,
  LDot[p1, p1] -> 0,
  LDot[p2, p2] -> 0,
  LDot[p1, p2] -> MW^2/2,
  LDot[k, p1] -> MW^2/2,
  LDot[k, p2] -> MW^2/2
};

trace = Spur[
  LDot[p2, \[Gamma]],
  LTensor[\[Gamma], \[Mu]], \[DoubleStruckCapitalP]L,
  LDot[p1, \[Gamma]],
  LTensor[\[Gamma], \[Nu]], \[DoubleStruckCapitalP]L
];

scalarizeRules = {
  LTensor[g, \[Nu], \[Nu]] -> \[ScriptD],
  LTensor[g, {p1}, {p2}] -> LDot[p1, p2],
  LTensor[g, {p2}, {p1}] -> LDot[p1, p2],
  LTensor[g, \[Mu], \[Nu]] LTensor[_, \[Mu], \[Nu], {p1}, {p2}] -> 0
};

transversePart = Contract[trace (-LTensor[g, \[Mu], \[Nu]])];
longitudinalPart = Contract[trace LTensor[k, \[Mu]] LTensor[k, \[Nu]]/MW^2];
totalPart = Contract[
  trace (-LTensor[g, \[Mu], \[Nu]]
         + LTensor[k, \[Mu]] LTensor[k, \[Nu]]/MW^2)
];

transverseResult = transversePart /. scalarizeRules /. onShell // LoopRefine;
longitudinalResult = longitudinalPart /. scalarizeRules /. onShell // LoopRefine;
totalResult = totalPart /. scalarizeRules /. onShell // LoopRefine;
```

### Expected Output

- `longitudinalResult = 0`
- `totalResult = 2 MW^2`

### Notes

- This example checks the **minimal `Wff'` left-handed-current structure**, not
  every electroweak case with masses / CKM / general `R_\[Xi]`
- This example does not separately test the overall vertex sign. The current
  project's overall `+i (g/\sqrt{2})` convention for `Wff'` is fixed by
  `standard-theories.md`
- If final-state fermion masses are kept, the longitudinal term generally no
  longer vanishes automatically
- If the user switches to another Z/W chirality or axial convention, state that
  convention explicitly in the analysis first

---

## §EW2: `e^+ e^- -> \mu^+ \mu^-` (tree diagram, minimal `A-Z` interference check)

### User Input (simulated)

> Give me a minimal electroweak tree-level example that checks the photon-Z
> s-channel interference term in `e^+ e^- -> \mu^+ \mu^-`, confirming that the
> Dirac-chiral structures of `\gamma ff` / `Zff` and the Mandelstam routing are
> correct.

### Physics Analysis

- **Theory framework:** Standard Model electroweak neutral current
- **Calculation target:** Check the tree-level interference kernel between
  `\gamma ff` and `Zff` with massless external lines
- **Process:** `e^-(p1) + e^+(p2) -> \mu^-(p3) + \mu^+(p4)`
- **Vertex structure:**
  - Photon: `LTensor[\[Gamma], \[Mu]]`
  - Z: `LTensor[\[Gamma], \[Nu]], gL \[DoubleStruckCapitalP]L + gR \[DoubleStruckCapitalP]R`
- **Kinematics:**
  - `p_i^2 = 0`
  - `s = (p1 + p2)^2`
  - `t = (p1 - p3)^2`
  - `u = (p1 - p4)^2`
  - `s + t + u = 0`
- **Key checks:**
  1. The interference kernel simplifies to
     `4 ((gRe gLmu + gLe gRmu) t^2 + (gLe gLmu + gRe gRmu) u^2)`
  2. The result contains only `t^2` / `u^2` combinations, consistent with the
     neutral-current chiral structure in the massless limit

### Code

```mathematica
(* ============================================================ *)
(* A-Z interference kernel in e+ e- -> mu+ mu- at tree level    *)
(* Project-local minimal electroweak validation example          *)
(* ============================================================ *)

<< X`

kinematics = {
  LDot[p1, p1] -> 0,
  LDot[p2, p2] -> 0,
  LDot[p3, p3] -> 0,
  LDot[p4, p4] -> 0,
  LDot[p1, p2] -> s/2,
  LDot[p3, p4] -> s/2,
  LDot[p1, p3] -> -t/2,
  LDot[p2, p4] -> -t/2,
  LDot[p1, p4] -> -u/2,
  LDot[p2, p3] -> -u/2,
  s + t + u -> 0
};

eTrace = Spur[
  LDot[p2, \[Gamma]],
  LTensor[\[Gamma], \[Mu]],
  LDot[p1, \[Gamma]],
  LTensor[\[Gamma], \[Nu]],
  gLe \[DoubleStruckCapitalP]L + gRe \[DoubleStruckCapitalP]R
];

muTrace = Spur[
  LDot[p3, \[Gamma]],
  LTensor[\[Gamma], \[Mu]],
  gLmu \[DoubleStruckCapitalP]L + gRmu \[DoubleStruckCapitalP]R,
  LDot[p4, \[Gamma]],
  LTensor[\[Gamma], \[Nu]]
];

interferenceKernel = Contract[eTrace muTrace] /. kinematics // LoopRefine;
```

### Expected Output

- `interferenceKernel = 4 ((gRe gLmu + gLe gRmu) t^2 + (gLe gLmu + gRe gRmu) u^2)`

### Notes

- This example checks the **chiral structure and Mandelstam routing of
  `\gamma ff` / `Zff` in neutral-current tree diagrams**
- It does not separately fix conventions such as overall field redefinitions.
  The project's absolute sign conventions are still those in
  `standard-theories.md`
- If external masses are retained, or if observables such as forward-backward
  asymmetries are analyzed next, modify the on-shell relations and coupling
  definitions accordingly
