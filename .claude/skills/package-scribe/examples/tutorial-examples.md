# Package-X Calculation Examples

This file provides tree-level and one-loop calculation examples aligned with
Package-X tutorial.pdf §4.2 and §5.1-§5.5. Each example includes simulated user
input, physics analysis, complete code, expected output, and notes.

---

## Contents

§0. Z→ff̄ decay width (tree diagram, Spur + Contract) -> tutorial §4.2
§1. QED vacuum polarization (Spur) -> tutorial §5.1
§2. H→gg decay rate (Spur + multiple diagrams) -> tutorial §5.2
§3. Electron self-energy (DiracMatrix + covariant gauge) -> tutorial §5.3
§4. Anomalous magnetic moment g-2 (Projector) -> tutorial §5.4
§5. QCD Γ*→qq̄ vertex correction (FermionLine) -> tutorial §5.5

---

## §0: Z→ff̄ Decay Width (tree diagram)

### User Input (simulated)

> Compute the tree-level Standard Model result for Z boson decay into a fermion
> pair f f̄, and provide a `.wl` example using Package-X for the Dirac trace.

### Physics Analysis

- **Theory framework:** Standard Model electroweak sector
- **Calculation target:** Tree-level spin-summed squared amplitude for Z→ff̄,
  then use it to write the partial decay width
- **Feynman diagram:** One Zff̄ vertex, no loop integral
- **Method:** Use `Spur` for the Dirac trace, `Contract` with the Z polarization
  sum tensor, and finally `LoopRefine` to safely take `d -> 4`
- **Vertex structure:** This example writes the vertex with the convention
  `\gamma^\mu (gV \[DoubleStruckOne] - gA \[Gamma]5)`, so the two fermion
  chains contain `gV \[DoubleStruckOne] - gA \[Gamma]5` and
  `gV \[DoubleStruckOne] + gA \[Gamma]5`, respectively
- **Key kinematics:** `k^2 = mZ^2`, `p1^2 = p2^2 = mf^2`,
  `p1.p2 = (mZ^2 - 2 mf^2)/2`

### Code

```mathematica
(* ============================================================ *)
(* Dirac/Lorentz part of squared amplitude for Z -> f fbar      *)
(* Aligned with Package-X tutorial §4.2                          *)
(* ============================================================ *)

<< X`

(* --- On-shell conditions --- *)
onShell = {
  LDot[k, k] -> mZ^2,
  LDot[p1, p1] -> mf^2,
  LDot[p2, p2] -> mf^2,
  LDot[p1, p2] -> (mZ^2 - 2 mf^2)/2,
  LDot[k, p1] -> mZ^2/2,
  LDot[k, p2] -> mZ^2/2
};

(* --- Dirac trace --- *)
trace = Spur[
  LDot[p2, \[Gamma]] + mf \[DoubleStruckOne],
  LTensor[\[Gamma], \[Mu]],
  gV \[DoubleStruckOne] - gA \[Gamma]5,
  LDot[p1, \[Gamma]] - mf \[DoubleStruckOne],
  gV \[DoubleStruckOne] + gA \[Gamma]5,
  LTensor[\[Gamma], \[Nu]]
];

(* --- Contract with the Z polarization-sum tensor --- *)
diracLorentzPart = Contract[
  trace (-LTensor[g, \[Mu], \[Nu]]
         + LTensor[k, \[Mu]] LTensor[k, \[Nu]]/mZ^2)
];

(* --- Explicitly scalarize tensor structures that remain in script output --- *)
scalarizeRules = {
  LTensor[g, \[Nu], \[Nu]] -> \[ScriptD],
  LTensor[g, {p1}, {p2}] -> LDot[p1, p2],
  LTensor[g, {p2}, {p1}] -> LDot[p1, p2],
  LTensor[g, \[Mu], \[Nu]] LTensor[_, \[Mu], \[Nu], {p1}, {p2}] -> 0
};

(* --- Substitute on-shell conditions and safely take d -> 4 --- *)
coreResult = diracLorentzPart /. scalarizeRules /. onShell // LoopRefine // Collect[#, {mf, mZ}] &;

(* --- Restore the full spin-summed |M|^2 delivered by this code --- *)
prefactor = Nc e^2 / (3 Sin[2 \[Theta]W]^2);
finalResult = prefactor coreResult;

Print["coreResult = ", coreResult];
Print["prefactor = ", prefactor];
Print["finalResult = ", finalResult];

(* If the user also wants the partial decay width Gamma(Z -> f fbar),
   multiply further by the two-body phase-space factor and 1/(2 mZ). *)
```

### Expected Output

- `coreResult = 4 (-4 gA^2 + 2 gV^2) mf^2 + 4 (gA^2 + gV^2) mZ^2`
- `prefactor = Nc e^2 / (3 Sin[2 \[Theta]W]^2)`
- `finalResult = prefactor \[Times] coreResult`, the full spin-summed `|M|^2`
  restored at the current code layer
- If the real target is the partial decay width, multiply further by two-body
  phase space and `1/(2 mZ)`

### Notes

- This is a **tree-level** example; it does not need `LoopIntegrate`
- Still use `LoopRefine` to safely take `d -> 4`; do not write
  `/. \[ScriptD] -> 4` manually
- Scalar constants such as `mf` must be written as `mf \[DoubleStruckOne]` before
  they can be added to Dirac matrices
- The Z polarization sum is
  `-g^{\[Mu]\[Nu]} + k^\[Mu] k^\[Nu]/mZ^2`; do not use the massless gauge-boson
  `-g^{\[Mu]\[Nu]}` form directly
- In `.wl` script `InputForm` output, structures such as
  `LTensor[g, {p1}, {p2}]` and `LTensor[g, \[Nu], \[Nu]]` may remain after
  `Contract`. Here `scalarizeRules` explicitly maps them back to dot products
  and the dimension, and sets contractions between symmetric metrics and
  antisymmetric Levi-Civita tensors to zero
- If the user only wants the tutorial-matched intermediate structure, use
  `coreResult` as the main result
- If the user really wants the partial decay width, continue from `finalResult`
  and add two-body phase space plus `1/(2 mZ)`

---

## §1: QED Vacuum Polarization

### User Input (simulated)

> Compute the one-loop vacuum polarization tensor Π^μν(q) in QED using
> dimensional regularization.

### Physics Analysis

- **Theory framework:** QED
- **Calculation target:** Photon self-energy (vacuum polarization tensor)
- **Feynman diagram:** One closed electron loop with two external photon legs
  carrying momentum q
- **Method:** `Spur` (closed fermion loop, Dirac trace required)
- **Expected structure:** By the Ward identity,
  $\Pi^{\mu\nu} = (q^2 g^{\mu\nu} - q^\mu q^\nu)\Pi(q^2)$

### Code

```mathematica
(* ============================================================ *)
(* QED one-loop vacuum polarization tensor                      *)
(* Aligned with Package-X tutorial §5.1                          *)
(* ============================================================ *)

(* --- Load Package-X --- *)
<< X`

(* --- Construct integrand --- *)
(* Feynman diagram: closed electron loop with two photon vertices *)
(* Vertex factors: LTensor[\[Gamma], \[Mu]] and LTensor[\[Gamma], \[Nu]] *)
(* Propagators: {k, m} and {k+q, m} (two electron propagators) *)
(* Closed loop trace: use Spur *)
numerator = Spur[
  LTensor[\[Gamma], \[Mu]],
  LDot[k, \[Gamma]] + m \[DoubleStruckOne],
  LTensor[\[Gamma], \[Nu]],
  LDot[k + q, \[Gamma]] + m \[DoubleStruckOne]
];

(* --- Loop integral --- *)
(* Note: result does not include i/(16 Pi^2) or coupling e^2 *)
amplitude = LoopIntegrate[numerator, k, {k, m}, {k + q, m}];

(* --- Extract transverse component (before LoopRefine!) --- *)
ampTransverse = Transverse[amplitude, q];

(* --- Simplify --- *)
result = LoopRefine[ampTransverse];

(* --- Verify Ward identity --- *)
(* The longitudinal part should be zero *)
ampLongitudinal = Longitudinal[amplitude, q];
wardCheck = LoopRefine[ampLongitudinal] // Simplify;
Print["Ward identity check (should be 0): ", wardCheck];

(* --- Output result --- *)
Print["Vacuum polarization function \[CapitalPi](q\[Superscript]2): ", result];
```

### Expected Output

- Result contains `DiscB[LDot[q,q], {m, m}]`
- Contains a $1/\epsilon$ UV pole (dimensional regularization)
- Ward identity check: longitudinal part is zero

### Notes

- `Transverse`/`Longitudinal` **must be called before `LoopRefine`**
- The full physical result must be multiplied by
  $(-1) \times e^2 \times \frac{i}{16\pi^2}$ (the $-1$ comes from the fermion
  loop)
- `DiscB` can be expanded into logarithms with `DiscExpand`

---

## §2: H→gg Decay Rate

### User Input (simulated)

> Compute the Standard Model amplitude for Higgs decay into two gluons through a
> top-quark loop, keeping finite top mass m_t.

### Physics Analysis

- **Theory framework:** Standard Model (Higgs + QCD)
- **Calculation target:** H→gg decay amplitude (top-quark loop contribution)
- **Feynman diagram:** Two triangle diagrams (top-quark loop with two gluon
  legs and one Higgs leg)
- **Method:** `Spur` (closed fermion loop)
- **Color factor:** $\text{Tr}(T^a T^b) = T_F \delta^{ab} =
  \frac{1}{2}\delta^{ab}$, factored out manually

### Code

```mathematica
(* ============================================================ *)
(* H -> gg decay amplitude (top-quark triangle loop)             *)
(* Aligned with Package-X tutorial §5.2                          *)
(* ============================================================ *)

<< X`

(* --- Diagram 1: first triangle with momentum routing as in tutorial Fig. 2 --- *)
num1 = Spur[
  LDot[k - q, \[Gamma]] + mt \[DoubleStruckOne],
  LTensor[\[Gamma], \[Nu]],
  LDot[k - p1, \[Gamma]] + mt \[DoubleStruckOne],
  LTensor[\[Gamma], \[Mu]],
  LDot[k, \[Gamma]] + mt \[DoubleStruckOne]
];

amp1 = LoopIntegrate[num1, k, {k - q, mt}, {k - p1, mt}, {k, mt}];

(* --- Diagram 2: exchange the two external gluons --- *)
num2 = Spur[
  LDot[k - q, \[Gamma]] + mt \[DoubleStruckOne],
  LTensor[\[Gamma], \[Mu]],
  LDot[k - p2, \[Gamma]] + mt \[DoubleStruckOne],
  LTensor[\[Gamma], \[Nu]],
  LDot[k, \[Gamma]] + mt \[DoubleStruckOne]
];

amp2 = LoopIntegrate[num2, k, {k - q, mt}, {k - p2, mt}, {k, mt}];

(* --- Combine first, then substitute on-shell decay kinematics and transversality --- *)
totalAmplitude = amp1 + amp2;

prepped = totalAmplitude /. {
  LDot[p1, p1] -> 0,
  LDot[p2, p2] -> 0,
  LDot[q, q] -> mH^2,
  q -> p1 + p2,
  LDot[p1, q] -> mH^2/2,
  LDot[p2, q] -> mH^2/2
} /. {
  LTensor[p1, \[Mu]] -> 0,
  LTensor[p2, \[Nu]] -> 0
};

result = LoopRefine[prepped] // Simplify;

(* --- Output --- *)
Print["H->gg amplitude (without color factor and couplings): ", result];
(* Full result must be multiplied by: *)
(* (-1) * (mt/v) * gs^2 * TF * i/(16 Pi^2) *)
(* where TF = 1/2, v ~ 246 GeV *)
```

### Expected Output

- The result factorizes into
  $(-2 p_1^\nu p_2^\mu + m_H^2 g^{\mu\nu})$ times a scalar coefficient
  depending only on $m_H, m_t$
- After `LoopRefine // Simplify`, the result is finite and has no $1/\epsilon$
- In the $m_t \to \infty$ limit it should reproduce the known low-energy
  effective coupling

### Notes

- The two triangle diagrams correspond to the two orientations of the fermion
  loop; using the momentum routing from tutorial Fig. 2 is the most stable path
- The color factor $T_F \delta^{ab}$ must be multiplied manually
- The Higgs-top coupling $m_t/v$ is not included in `LoopIntegrate` output
- The on-shell and transversality conditions here should be substituted
  explicitly before `LoopRefine // Simplify`

---

## §3: Electron Self-Energy

### User Input (simulated)

> Compute the one-loop electron self-energy Σ(p) in QED using a general
> covariant gauge with gauge parameter ξ.

### Physics Analysis

- **Theory framework:** QED, general covariant gauge
- **Calculation target:** Electron self-energy
  $\Sigma(p) = A(p^2)\slashed{p} + B(p^2)m$
- **Feynman diagram:** Photon loop attached to an electron line
- **Method:** `DiracMatrix` (off-shell external fermion)
- **Gauge dependence:** Use parameter $\xi$; Feynman gauge corresponds to
  $\xi = 1$

### Code

```mathematica
(* ============================================================ *)
(* QED electron self-energy (general covariant gauge)            *)
(* Aligned with Package-X tutorial §5.3                          *)
(* ============================================================ *)

<< X`

(* --- Construct integrand --- *)
(* Feynman-gauge part (Xi = 1 term) *)
numFeynman = DiracMatrix[
  LTensor[\[Gamma], \[Mu]],
  LDot[k + p, \[Gamma]] + m \[DoubleStruckOne],
  LTensor[\[Gamma], \[Mu]]
];

ampFeynman = LoopIntegrate[numFeynman, k, {k, 0}, {k + p, m}];

(* Gauge correction term ((1-Xi) term, with an extra photon propagator power) *)
numGauge = DiracMatrix[
  LDot[k, \[Gamma]],
  LDot[k + p, \[Gamma]] + m \[DoubleStruckOne],
  LDot[k, \[Gamma]]
];

ampGauge = LoopIntegrate[numGauge, k, {k, 0, 2}, {k + p, m}];

(* --- Combine --- *)
totalAmplitude = ampFeynman - (1 - \[Xi]) ampGauge;

(* --- Simplify --- *)
result = LoopRefine[totalAmplitude];

(* --- Decompose into Dirac structures --- *)
(* Sigma(p) = A(p^2) p/ + B(p^2) m *)
(* Use FermionLineExpand if applicable *)

(* --- Output --- *)
Print["Electron self-energy (general covariant gauge): ", result];
(* Full result must be multiplied by: e^2 * i/(16 Pi^2) *)
```

### Expected Output

- Result contains both $\slashed{p}$ and $m$ Dirac structures
- $\xi$ appears in coefficients (gauge dependence)
- Contains a $1/\epsilon$ UV pole

### Notes

- The photon propagator in a general covariant gauge must be split into two
  terms
- `{k, 0, 2}` denotes the squared photon propagator (the $1/k^4$ term)
- On-shell mass-renormalization condition: $\Sigma(p)|_{p/=m} = 0$

---

## §4: Anomalous Magnetic Moment (g-2)

### User Input (simulated)

> Compute the one-loop electron anomalous magnetic moment a_e = (g-2)/2 in QED.

### Physics Analysis

- **Theory framework:** QED
- **Calculation target:** Pauli form factor $F_2(0)$ from the electron-photon
  vertex correction, restoring the electron anomalous magnetic moment
  $a_e=(g-2)/2$
- **Feynman diagram:** One-loop correction to the electron-photon vertex
- **Method:** `Projector` (direct projection of Pauli form factor $F_2$)
- **Expected result:** Schwinger result $a_e = \alpha/(2\pi)$

### Code

```mathematica
(* ============================================================ *)
(* QED electron anomalous magnetic moment (g-2)/2                *)
(* Aligned with Package-X tutorial §5.4                          *)
(* Schwinger result: a_e = \[Alpha]/(2\[Pi])                     *)
(* ============================================================ *)

<< X`

(* --- Construct integrand --- *)
(* Vertex correction: electron line with one electron-photon vertex on each side *)
(* External electrons on-shell: p1^2 = p2^2 = m^2 *)
(* Photon momentum q = p2 - p1 *)
numerator = Contract[
  Spur[
    LTensor[\[Gamma], \[Rho]],
    LDot[p2 - k, \[Gamma]] + m \[DoubleStruckOne],
    LTensor[\[Gamma], \[Mu]],
    LDot[p1 - k, \[Gamma]] + m \[DoubleStruckOne],
    LTensor[\[Gamma], \[Rho]],
    Projector["F2", \[Mu]][{p1, m}, {p2, m}]
  ]
] /. {
  LDot[p1, p1] -> m^2,
  LDot[p2, p2] -> m^2,
  LDot[p1, p2] -> -LDot[q, q]/2 + m^2
} // Simplify;

(* --- Loop integral --- *)
amplitude = LoopIntegrate[numerator, k,
  {p2 - k, m},
  {p1 - k, m},
  {k, 0}
] /. {
  LDot[p1, p1] -> m^2,
  LDot[p2, p2] -> m^2,
  LDot[p1, p2] -> -LDot[q, q]/2 + m^2
} // Simplify // Cancel;

(* --- Only then take the q^2 -> 0 limit and run LoopRefine --- *)
coreResult = LoopRefine[amplitude /. LDot[q, q] -> 0];

(* --- Restore the physical anomalous magnetic moment --- *)
prefactor = e^2 / (16 \[Pi]^2);
finalResult = prefactor coreResult;

Print["coreResult = ", coreResult];
Print["prefactor = ", prefactor];
Print["finalResult = ", finalResult];
(* Expected: coreResult = F2(0) = 2, finalResult = a_e = e^2/(8 Pi^2) = Alpha/(2 Pi) *)
```

### Expected Output

- `coreResult = F2(0) = 2`
- `prefactor = e^2/(16 \[Pi]^2)`
- `finalResult = a_e = e^2/(8 \[Pi]^2) = \[Alpha]/(2 \[Pi])` (Schwinger result)

### Notes

- `Projector["F2", \[Mu]][{p1,m}, {p2,m}]` directly extracts $F_2$; `"Pauli"`
  is its equivalent alias
- In `.wl` scripts, using the repeated dummy index `\[Rho]` for metric
  contraction of the virtual photon propagator is more robust than manually
  writing extra `LTensor[g, ...]`
- First go on-shell and use `Simplify`/`Cancel` to remove removable
  singularities, then take the $q^2 \to 0$ limit
- The result here has no $1/\epsilon$ pole ($F_2$ is UV finite)
- If the user only asks "what is the Package-X output for `F2(0)`?", use
  `coreResult` as the main result; if the user asks for the electron anomalous
  magnetic moment, report `finalResult = a_e` as the main result

---

## §5: QCD Γ*→qq̄ Vertex Correction

### User Input (simulated)

> Compute the one-loop QCD vertex correction Γ*→qq̄ for an off-shell photon into
> a quark-antiquark pair, in the massless-quark limit with on-shell external
> quarks.

### Physics Analysis

- **Theory framework:** QCD
- **Calculation target:** One-loop correction to the $\gamma^* \to q\bar{q}$
  vertex
- **Feynman diagram:** Gluon-exchange loop attached to the quark-photon vertex
- **Method:** `FermionLine` (external quarks on-shell, massless limit here)
- **Color factor:** $C_F = 4/3$, factored out manually

### Code

```mathematica
(* ============================================================ *)
(* QCD \[Gamma]*->qq\[OverBar] vertex correction                 *)
(* Aligned with Package-X tutorial §5.5                          *)
(* ============================================================ *)

<< X`

(* --- Construct integrand --- *)
(* Gluon-exchange correction diagram *)
(* Following tutorial §5.5, take the massless-quark limit: p1^2 = p2^2 = 0 *)
(* Color factor CF = 4/3 is handled separately *)
numerator = FermionLine[
  {1, p1, 0},
  {-1, p2, 0},
  DiracMatrix[
    LTensor[\[Gamma], \[Rho]],
    LDot[p1 + k, \[Gamma]],
    LTensor[\[Gamma], \[Mu]],
    LDot[-p2 + k, \[Gamma]],
    LTensor[\[Gamma], \[Rho]]
  ]
] // Contract;

(* --- Loop integral --- *)
integral = LoopIntegrate[numerator, k, {p1 + k, 0}, {-p2 + k, 0}, {k, 0}] /. {
  LDot[p1, p1] -> 0,
  LDot[p2, p2] -> 0,
  LDot[p1, p2] -> LDot[q, q]/2
};

(* --- Simplify --- *)
result = LoopRefine[integral] // Contract // FermionLineExpand // Simplify;

(* --- Output --- *)
Print["Vertex correction (without color factor and couplings): ", result];
(* Full result must be multiplied by: CF * gs^2 * i/(16 Pi^2) *)
(* where CF = 4/3 *)
```

### Expected Output

- The result is proportional to the tree-level Dirac structure and can be
  organized as
  `FermionLine[{1,p1,0}, {-1,p2,0}, DiracMatrix[LTensor[\[Gamma], \[Mu]]]]`
  times a scalar coefficient
- Contains $1/\epsilon$ UV poles and IR divergences
- Same color-independent structure as the QED vertex correction, differing only
  by the color factor

### Notes

- `FermionLine` automatically uses the on-shell Dirac equation for
  simplification
- The color factor $C_F$ is not in the Package-X output and must be multiplied
  manually
- This example already takes the massless-quark limit, so it contains both UV
  and IR poles
- If finite quark mass is needed, rewrite the propagator masses and on-shell
  conditions separately; do not simply replace the `0` entries here by `m_q`
