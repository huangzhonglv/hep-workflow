# Standard-Theory Feynman Rules (Package-X Representation)

This file provides Package-scribe with standard-theory Feynman rules, common
on-shell conditions, and Package-X translation patterns.

- Read it only when the user is clearly working in **QED / QCD / Standard Model
  / Yukawa**
- If the user provides a custom Lagrangian, do not rely on this file; first read
  `custom-lagrangian-validation.md`, then `packagex-reference.md` §5
- This file focuses on rules needed for **tree-level and one-loop** work. Higher
  dimensional operators, anomalous couplings, Majorana fermions, EFT Wilson
  coefficients, and non-standard gauge fixing are outside the default coverage

---

## Conventions

### Unified Conventions

1. **All vertex momenta are incoming by default.**
   If an external line is outgoing in the actual amplitude, reverse the
   corresponding momentum in the vertex or propagator as appropriate.

2. **Package-X handles only Lorentz / Dirac / tensor algebra.**
   Coupling constants, overall `i`, the extra `-1` from a closed fermion loop,
   color factors, and symmetry factors should be explicitly tracked by the
   higher-level code.

3. **Propagator lists encode denominators only.**
   Numerator structures such as `\slashed{k} + m`, `g^{\mu\nu}`, and
   `k^\mu k^\nu` must be written inside `Spur`, `DiracMatrix`, `FermionLine`, or
   explicit tensor expressions.

4. **`.wl` scripts always use `\[Name]` forms.**
   Examples: `LTensor[\[Gamma], \[Mu]]`, `\[DoubleStruckOne]`,
   `\[DoubleStruckCapitalP]L`, `\[Gamma]5`.

5. **Non-Feynman gauges usually require propagator splitting.**
   For massless gauge bosons, the longitudinal part often corresponds to
   `{k, 0, 2}`. For massive gauge bosons, the longitudinal part usually
   corresponds to two different propagator masses, such as `{k, MW}` and
   `{k, Sqrt[\[Xi]W] MW}`.

6. **The definitions of spacetime signature, `\[Sigma]^{\mu\nu}`, and
   `\[Gamma]5` inherit the "spacetime convention" in
   `packagex-reference.md`.**
   If the user uses a different convention, state it explicitly before code
   generation and switch consistently.

### Common Package-X Snippets

| Physics structure | `.wl` form |
|----------|------------|
| Vector vertex `\gamma^\mu` | `LTensor[\[Gamma], \[Mu]]` |
| Left-handed current `\gamma^\mu P_L` | `LTensor[\[Gamma], \[Mu]], \[DoubleStruckCapitalP]L` |
| Right-handed current `\gamma^\mu P_R` | `LTensor[\[Gamma], \[Mu]], \[DoubleStruckCapitalP]R` |
| Scalar Yukawa vertex | `\[DoubleStruckOne]` |
| Pseudoscalar Yukawa vertex | `\[Gamma]5` |
| Fermion completeness relation | `LDot[p, \[Gamma]] + m \[DoubleStruckOne]` |

### Common Tree-Level Polarization Sums

```mathematica
(* Massless gauge boson; usable in Feynman gauge for gauge-invariant amplitudes *)
-LTensor[g, \[Mu], \[Nu]]

(* Massive spin-1 boson *)
-LTensor[g, \[Mu], \[Nu]] + LTensor[k, \[Mu]] LTensor[k, \[Nu]]/M^2
```

### Validation Boundary

| Area | Repository status | Automation guidance | Notes |
|------|---------|-----------|------|
| QED fermion-photon sector | **Validated by examples** | May generate directly | tutorial-examples §1, §3, §4 |
| SM top Yukawa to `H -> gg` | **Validated by examples** | May generate directly | tutorial-examples §2 |
| QCD quark-gluon sector | **Validated by examples** | May generate directly | tutorial-examples §5 (currently validates massless quarks) |
| QCD pure-gluon / ghost sector | **Formulas specified, no end-to-end example yet** | Explicitly state momentum-flow / diagram-set assumptions before generating | Must state pure-gauge / ghost inclusion and ghost momentum convention |
| `Wff'` tree diagram (left-handed current, massless final state) | **Minimally validated** | May generate directly | `examples/electroweak-minimal-examples.md` §EW1 and `verification/examples/ex6_ew_w_to_lnu_tree.wl` |
| Neutral-current tree diagram (`\[Gamma]ff + Zff`, massless external lines) | **Minimally validated** | May generate directly | `examples/electroweak-minimal-examples.md` §EW2 and `verification/examples/ex7_ew_ee_to_mumu_az_tree.wl` |
| Other basic electroweak vertices and propagators (`AWW`, `ZWW`, `HWW`, `HZZ`, and `A/W/Z/H/Goldstone/ghost` propagators) | **Formulas specified, no end-to-end example yet** | Confirm convention first, then generate | Suitable for tree diagrams and simple one-loop work; if retaining general `R_\[Xi]`, state propagator splitting and diagram-set scope |
| Electroweak Goldstone / ghost / mixing interaction vertices | **Complete formulas not specified here** | **Do not automate from this file alone** | Includes Goldstone-fermion, EW ghost-gauge-boson, ghost-Goldstone-Higgs, and gauge-scalar mixing |
| Generic Yukawa with `\[Gamma]5` | **Syntax-level support** | Tree-level or four-dimensional intermediate results may be generated; scheme-sensitive one-loop work should not be generated directly | When `\[Gamma]5` appears, also read `packagex-reference.md` §6 |

---

## 1. QED

### 1.1 Lagrangian And Coupling Convention

For a Dirac fermion with charge `Q_f`,

$$
\mathcal{L}_{\text{QED}}
= \bar\psi (i\slashed{\partial} - m)\psi
- e Q_f \bar\psi \gamma^\mu A_\mu \psi
- \frac{1}{4}F_{\mu\nu}F^{\mu\nu}
- \frac{1}{2\xi_A}(\partial_\mu A^\mu)^2 .
$$

This file defaults to the vertex convention corresponding to
`D_\mu = \partial_\mu + i e Q_f A_\mu`, so the fermion-photon vertex is
`-i e Q_f \gamma^\mu`.

### 1.2 Vertex

| Vertex | Feynman rule | Package-X form (without coupling) |
|------|-------------|----------------------------|
| `A_\[Mu] \bar\psi \psi` | `-i e Q_f \gamma^\mu` | `LTensor[\[Gamma], \[Mu]]` |

### 1.3 Propagators

| Particle | Feynman rule | Package-X propagator item |
|------|-------------|--------------------|
| Fermion (mass `m`) | `i (\slashed{k}+m)/(k^2-m^2)` | `{k, m}` |
| Photon (Feynman gauge) | `-i g^{\mu\nu}/k^2` | `{k, 0}` |
| Photon (general covariant gauge) | `-i[g^{\mu\nu}-(1-\xi_A)k^\mu k^\nu/k^2]/k^2` | Feynman part `{k, 0}`; longitudinal part paired with `{k, 0, 2}` |

For a general covariant gauge, a common numerator form is:

```mathematica
LTensor[g, \[Mu], \[Nu]] LDot[k, k]
  - (1 - \[Xi]A) LTensor[k, \[Mu]] LTensor[k, \[Nu]]
```

paired with propagator list `{k, 0, 2}`.

### 1.4 External Lines And On-Shell Conditions

```mathematica
(* External electron / muon / massive charged fermion *)
LDot[p, p] -> m^2

(* External photon *)
LDot[q, q] -> 0

(* If the amplitude still keeps polarization vectors *)
LDot[\[CurlyEpsilon], q] -> 0

(* If the amplitude is a tensor with open Lorentz indices,
   transversality may be used when contracting with external polarizations *)
LTensor[q, \[Mu]] -> 0
```

Common two-body decay/scattering relations:

```mathematica
(* A particle of mass M decays into two massless external lines *)
q -> p1 + p2
LDot[p1, p1] -> 0
LDot[p2, p2] -> 0
LDot[q, q] -> M^2
LDot[p1, p2] -> M^2/2
```

### 1.5 Recommended Methods For Tree And One-Loop Calculations

| Calculation object | Typical structure | Recommended function |
|----------|----------|----------|
| Tree diagrams such as `e^+e^- \to \mu^+\mu^-`, `\gamma^* \to f\bar f` | Dirac trace after spin sum | `Spur` + `Contract` |
| Vacuum polarization `\Pi^{\mu\nu}(q)` | Closed fermion loop | `Spur` |
| Fermion self-energy `\Sigma(p)` | Off-shell open line | `DiracMatrix` |
| On-shell vertex correction | On-shell open external lines | `FermionLine` |
| `g-2` / Pauli form factor | Vertex projection | `Projector["F2", \[Mu]]` |

### 1.6 QED Notes

- Abelian QED has **no three-photon, four-photon, or ghost vertices**
- If the user asks for nonlinear gauge, Pauli terms, anapole moments, or other
  extended couplings, do not continue with the default rules in this section;
  return to the custom-Lagrangian flow
- `Projector` calculations must remove removable singularities before taking
  `q^2 -> 0`

---

## 2. QCD

### 2.1 Lagrangian And Color Conventions

$$
\mathcal{L}_{\text{QCD}} =
-\frac{1}{4}G^a_{\mu\nu} G^{a\mu\nu}
 + \sum_q \bar q (i\slashed{D} - m_q) q
 - \frac{1}{2\xi_g}(\partial_\mu G^{a\mu})^2
 + \partial^\mu \bar c^a (D_\mu c)^a .
$$

- Color group: `SU(N_c)`, default `N_c = 3`
- Generators: `T^a`
- Structure constants: `f^{abc}`
- **Package-X does not perform color algebra**; handle `T^a`, `f^{abc}`,
  `\delta^{ab}`, `C_F`, etc. explicitly at the outer layer

### 2.2 Common Color Factors

| Symbol | Definition | `SU(3)` value |
|------|------|------------|
| `C_F` | `T^a T^a = C_F \mathbf{1}` | `4/3` |
| `C_A` | `f^{acd} f^{bcd} = C_A \delta^{ab}` | `3` |
| `T_F` | `Tr(T^a T^b) = T_F \delta^{ab}` | `1/2` |

Common overall factors:

- Quark-loop contribution to gluon self-energy: `n_f T_F \delta^{ab}`
- One-loop quark vertex correction: `C_F`
- Pure-gluon one-loop diagrams: commonly contain `C_A`

### 2.3 Propagators

| Particle | Feynman rule | Package-X propagator item |
|------|-------------|--------------------|
| Quark (mass `mq`) | `i(\slashed{k}+m_q)/(k^2-m_q^2)` | `{k, mq}` |
| Massless quark | Same with `m_q = 0` | `{k, 0}` |
| Gluon (Feynman gauge) | `-i \delta^{ab} g^{\mu\nu}/k^2` | `{k, 0}` |
| Gluon (general covariant gauge) | `-i \delta^{ab}[g^{\mu\nu}-(1-\xi_g)k^\mu k^\nu/k^2]/k^2` | Feynman part `{k, 0}`; longitudinal part `{k, 0, 2}` |
| Ghost | `i \delta^{ab}/k^2` | `{k, 0}` |

If keeping the general covariant-gauge gluon-propagator numerator explicitly,
write:

```mathematica
LTensor[g, \[Mu], \[Nu]] LDot[k, k]
  - (1 - \[Xi]g) LTensor[k, \[Mu]] LTensor[k, \[Nu]]
```

with propagator list `{k, 0, 2}`.

### 2.4 Quark-Gluon Vertex

| Vertex | Feynman rule | Package-X form (without coupling and color) |
|------|-------------|--------------------------------|
| `G^a_\[Mu] \bar q q` | `-i g_s T^a \gamma^\mu` | `LTensor[\[Gamma], \[Mu]]` |

This is the QCD rule with the strongest validation in the current repository;
see tutorial-examples §5.

### 2.5 Three-Gluon Vertex

For all momenta `p, q, r` **incoming to the vertex**, the Lorentz structure is

$$
V^{\mu\nu\rho}_{3g}(p,q,r) =
g^{\mu\nu}(p-q)^\rho
+ g^{\nu\rho}(q-r)^\mu
+ g^{\rho\mu}(r-p)^\nu .
$$

Complete rule:

$$
- i g_s f^{abc} V^{\mu\nu\rho}_{3g}(p,q,r) .
$$

The Lorentz part in Package-X can be written as:

```mathematica
LTensor[g, \[Mu], \[Nu]] (LTensor[p, \[Rho]] - LTensor[q, \[Rho]]) +
LTensor[g, \[Nu], \[Rho]] (LTensor[q, \[Mu]] - LTensor[r, \[Mu]]) +
LTensor[g, \[Rho], \[Mu]] (LTensor[r, \[Nu]] - LTensor[p, \[Nu]])
```

### 2.6 Four-Gluon Vertex

The complete rule can be written as the sum of three color structures:

$$
\begin{aligned}
V^{\mu\nu\rho\sigma}_{4g}
= -i g_s^2 [&
f^{abe} f^{cde} (g^{\mu\rho} g^{\nu\sigma} - g^{\mu\sigma} g^{\nu\rho}) \\
&+ f^{ace} f^{bde} (g^{\mu\nu} g^{\rho\sigma} - g^{\mu\sigma} g^{\nu\rho}) \\
&+ f^{ade} f^{bce} (g^{\mu\nu} g^{\rho\sigma} - g^{\mu\rho} g^{\nu\sigma}) ] .
\end{aligned}
$$

Because Package-X does not handle color structures, split the three terms and
write each Lorentz tensor separately:

```mathematica
L1 = LTensor[g, \[Mu], \[Rho]] LTensor[g, \[Nu], \[Sigma]]
   - LTensor[g, \[Mu], \[Sigma]] LTensor[g, \[Nu], \[Rho]];

L2 = LTensor[g, \[Mu], \[Nu]] LTensor[g, \[Rho], \[Sigma]]
   - LTensor[g, \[Mu], \[Sigma]] LTensor[g, \[Nu], \[Rho]];

L3 = LTensor[g, \[Mu], \[Nu]] LTensor[g, \[Rho], \[Sigma]]
   - LTensor[g, \[Mu], \[Rho]] LTensor[g, \[Nu], \[Sigma]];
```

Then multiply each by the corresponding `f f` color structure.

### 2.7 Ghost Sector

| Vertex | Convention | Package-X form (without coupling and color) |
|------|------|--------------------------------|
| `\bar c^a G^b_\[Mu] c^c` | If antighost momentum `p` flows into the vertex, the rule is `g_s f^{abc} p^\mu` | `LTensor[p, \[Mu]]` |

Notes:

- The overall sign of a ghost vertex depends on which ghost momentum you define
  as incoming
- If you use the ghost momentum rather than the antighost momentum, flip the
  overall sign
- If the user has not specified the ghost / antighost momentum convention, code
  comments must state the default convention used here: **antighost momentum
  flows into the vertex**
- For pure-gluon / ghost one-loop diagrams, explicitly state the momentum-flow
  convention in the output

### 2.8 Common On-Shell Conditions

```mathematica
(* Massless quarks *)
LDot[p1, p1] -> 0
LDot[p2, p2] -> 0

(* Massive quark *)
LDot[p, p] -> mq^2

(* If intermediate q = p1 + p2 or q = p1 - p2, define it process by process *)
LDot[p1, p2] -> LDot[q, q]/2          (* common for massless two-body vertices *)
LDot[p1, p2] -> m^2 - LDot[q, q]/2    (* common for equal-mass on-shell vertices *)
```

### 2.9 Recommended QCD Methods

| Calculation object | Recommended function | Notes |
|----------|----------|------|
| Quark-loop contribution to gluon self-energy | `Spur` | Multiply color factor `n_f T_F \delta^{ab}` manually |
| `\gamma^* / Z \to q\bar q` vertex correction | `FermionLine` | Current example validation covers the massless version |
| Quark self-energy | `DiracMatrix` or `FermionLine` | Depends on whether external lines are on-shell |
| Pure-gluon self-energy / vertex correction | Explicit tensor numerator + `LoopIntegrate` | No repository example yet; state assumptions in output |

### 2.10 QCD Notes

- **If the user only says "QCD vertex correction" but does not say whether to
  include pure-gauge / ghost diagrams, ask first.**
- If the user only cares about the tutorial §5.5-like
  `\gamma^* \to q\bar q`, default to **massless quarks + Feynman gauge**
- If the user asks for a general covariant gauge or the full QCD beta function,
  include the 3g / 4g / ghost sectors

---

## 3. Standard Model Electroweak And Higgs Sector

### 3.1 Unified Conventions

- `e = g s_W = g' c_W`
- `s_W \equiv \sin\theta_W`, `c_W \equiv \cos\theta_W`
- `P_L = (1-\gamma_5)/2`, `P_R = (1+\gamma_5)/2`
- Fermion charge is denoted `Q_f`
- Third component of weak isospin is denoted `T_3^f`
- For **overall signs and field definitions in the SM electroweak sector**, this
  file defines the canonical local convention. It uses the mass-eigenstate
  definitions
  `W^\[PlusMinus] = (W^1 \mp i W^2)/\sqrt{2}`,
  `A = - s_W W^3 + c_W B`, and
  `Z = c_W W^3 + s_W B`

For Z-fermion couplings, commonly use

$$
g_L^f = T_3^f - Q_f s_W^2,
\qquad
g_R^f = - Q_f s_W^2 .
$$

If rewriting the Z vertex in vector/axial form, Package-scribe recommends

$$
\gamma^\mu (g_V^f \mathbf{1} - g_A^f \gamma_5) ,
$$

with the following correspondence to chiral form:

$$
\gamma^\mu (g_L^f P_L + g_R^f P_R)
=
\gamma^\mu
\left(
\frac{g_L^f + g_R^f}{2}\mathbf{1}
-
\frac{g_L^f - g_R^f}{2}\gamma_5
\right) .
$$

Therefore, in this file's normalization,

$$
g_V^f = \frac{g_L^f + g_R^f}{2},
\qquad
g_A^f = \frac{g_L^f - g_R^f}{2}.
$$

If using notation `\gamma^\mu (g_V \mathbf{1} + g_A \gamma_5)`, flip the
definition of `g_A` and state that explicitly in the output.

Common charges and weak isospins for light fermions:

| Fermion | `Q_f` | `T_3^f` | `g_L^f` | `g_R^f` |
|--------|-------|---------|---------|---------|
| `\nu` | `0` | `+1/2` | `+1/2` | `0` |
| `e^-` | `-1` | `-1/2` | `-1/2 + s_W^2` | `s_W^2` |
| `u` | `+2/3` | `+1/2` | `+1/2 - 2 s_W^2/3` | `- 2 s_W^2/3` |
| `d` | `-1/3` | `-1/2` | `-1/2 + s_W^2/3` | `s_W^2/3` |

### 3.2 Fermion-Gauge-Boson Vertices

| Vertex | Feynman rule | Package-X form (without coupling) |
|------|-------------|----------------------------|
| `A_\[Mu] \bar f f` | `-i e Q_f \gamma^\mu` | `LTensor[\[Gamma], \[Mu]]` |
| `Z_\[Mu] \bar f f` | `+i (g/c_W) \gamma^\mu (g_L^f P_L + g_R^f P_R)` | `LTensor[\[Gamma], \[Mu]], gL \[DoubleStruckCapitalP]L + gR \[DoubleStruckCapitalP]R` |
| `W^+_\[Mu] \bar u_i d_j` | `+i (g/\sqrt{2}) V_{ij} \gamma^\mu P_L` | `LTensor[\[Gamma], \[Mu]], \[DoubleStruckCapitalP]L` |
| `W^-_\[Mu] \bar d_j u_i` | `+i (g/\sqrt{2}) V^*_{ij} \gamma^\mu P_L` | `LTensor[\[Gamma], \[Mu]], \[DoubleStruckCapitalP]L` |

If the process does not involve flavor mixing, take `V_{ij} = 1`.

### 3.3 Gauge-Boson Propagators

| Particle | Feynman gauge | Package-X propagator item |
|------|-------------|--------------------|
| Photon | `-i g^{\mu\nu}/k^2` | `{k, 0}` |
| `Z` | `-i g^{\mu\nu}/(k^2-M_Z^2)` | `{k, MZ}` |
| `W^\pm` | `-i g^{\mu\nu}/(k^2-M_W^2)` | `{k, MW}` |

The Higgs is a scalar and uses the §4.3 scalar propagator form `{k, MH}`.

In a general `R_\[Xi]` gauge, a massive vector propagator is

$$
\frac{-i}{k^2-M_V^2}
\left(
g^{\mu\nu}
- \frac{(1-\xi_V) k^\mu k^\nu}{k^2-\xi_V M_V^2}
\right) .
$$

In Package-X, write the propagator as "transverse part + longitudinal
correction". For `W`, using this file's convention where the overall `i` is
tracked separately:

```mathematica
(* transverse / Feynman-like part *)
numT = -LTensor[g, \[Mu], \[Nu]];
propsT = {{k, MW}};

(* longitudinal R_xi correction *)
numL = (1 - \[Xi]W) LTensor[k, \[Mu]] LTensor[k, \[Nu]];
propsL = {{k, MW}, {k, Sqrt[\[Xi]W] MW}};
```

Thus the longitudinal part must be split into **two denominators**, and the
front coefficient `(1 - \[Xi]W)` must not be omitted.

Do not incorrectly write this as a single `{k, MW, 2}`, and do not forget that
it should vanish automatically when `\[Xi]W = 1`.

### 3.4 Goldstone And Ghost Propagators

In `R_\[Xi]` gauge:

| Field | Mass | Package-X propagator item |
|----|------|--------------------|
| `G^\[PlusMinus]` | `Sqrt[\[Xi]W] MW` | `{k, Sqrt[\[Xi]W] MW}` |
| `G^0` | `Sqrt[\[Xi]Z] MZ` | `{k, Sqrt[\[Xi]Z] MZ}` |
| `c_W^\[PlusMinus]` | `Sqrt[\[Xi]W] MW` | `{k, Sqrt[\[Xi]W] MW}` |
| `c_Z` | `Sqrt[\[Xi]Z] MZ` | `{k, Sqrt[\[Xi]Z] MZ}` |
| `c_A` | `0` | `{k, 0}` |

Feynman gauge corresponds to `\[Xi]W = \[Xi]Z = 1`; then Goldstone and ghost
masses equal the corresponding gauge-boson masses. In a linear covariant gauge,
the photon ghost `c_A` usually decouples from physical amplitudes.

### 3.5 Gauge-Boson Self-Interactions

`AWW` and `ZWW` vertices have the same Lorentz structure as the Yang-Mills
three-point vertex; only the coupling changes:

| Vertex | Overall coupling | Lorentz structure |
|------|----------|--------------|
| `A_\[Mu] W^+_\[Nu] W^-_\[Rho]` | `-i e` | Same as QCD three-gluon vertex |
| `Z_\[Mu] W^+_\[Nu] W^-_\[Rho]` | `+i g c_W` | Same as QCD three-gluon vertex |

Reuse the tensor template from QCD §2.5:

```mathematica
LTensor[g, \[Mu], \[Nu]] (LTensor[p, \[Rho]] - LTensor[q, \[Rho]]) +
LTensor[g, \[Nu], \[Rho]] (LTensor[q, \[Mu]] - LTensor[r, \[Mu]]) +
LTensor[g, \[Rho], \[Mu]] (LTensor[r, \[Nu]] - LTensor[p, \[Nu]])
```

### 3.6 Higgs-Gauge-Boson Vertices

| Vertex | Feynman rule | Package-X form (without coupling) |
|------|-------------|----------------------------|
| `H W^+_\[Mu] W^-_\[Nu]` | `i g M_W g^{\mu\nu}` | `LTensor[g, \[Mu], \[Nu]]` |
| `H Z_\[Mu] Z_\[Nu]` | `i (g M_Z / c_W) g^{\mu\nu}` | `LTensor[g, \[Mu], \[Nu]]` |

Equivalently, the overall couplings can be written as
`2 i M_W^2 / v` and `2 i M_Z^2 / v`.

### 3.7 Common On-Shell Conditions

```mathematica
LDot[pW, pW] -> MW^2
LDot[pZ, pZ] -> MZ^2
LDot[pA, pA] -> 0
LDot[pH, pH] -> MH^2
```

If a tree-level amplitude keeps open Lorentz indices and is later contracted
with external polarization vectors, use:

```mathematica
LTensor[pA, \[Mu]] -> 0
LTensor[pW, \[Mu]] -> 0   (* only in the context of contraction with physical polarization *)
LTensor[pZ, \[Mu]] -> 0   (* same *)
```

### 3.8 Electroweak Automation Boundary

Minimally validated rules that may be automated directly:

- `Wff'` (left-handed current tree diagram with massless final state)
- Neutral-current s-channel tree diagrams for `\gamma ff` and `Zff` (massless
  external lines)

Rules whose formulas are specified here and may be automated after confirming
convention:

- `AWW`
- `ZWW`
- `HWW`
- `HZZ`
- Propagators for `A / W / Z / H / Goldstone / ghost`

Rules for which this file does not give complete vertex formulas and which
**must not be automated from this file alone**:

- Goldstone-fermion vertices
- Electroweak ghost-gauge-boson vertices
- Electroweak ghost-Goldstone-Higgs mixed vertices
- Complete gauge-scalar mixing diagrams in a general `R_\[Xi]` gauge

If the user asks for these objects without providing conventions,
Package-scribe should ask first or explicitly state the convention it adopts.

---

## 4. Yukawa Couplings

### 4.1 Generic Scalar / Pseudoscalar Yukawa

A common form is

$$
\mathcal{L}_{Y}
= - y_S \phi \bar\psi \psi
- i y_P \phi \bar\psi \gamma_5 \psi .
$$

| Vertex | Feynman rule | Package-X form (without coupling) |
|------|-------------|----------------------------|
| `\phi \bar\psi \psi` | `-i y_S` | `\[DoubleStruckOne]` |
| `\phi \bar\psi i\gamma_5 \psi` | `-i y_P \gamma_5` | `\[Gamma]5` |

### 4.2 Generic Chiral Yukawa

If the user gives

$$
\mathcal{L} \supset - \phi \bar\psi_i (y_L P_L + y_R P_R) \psi_j + \text{h.c.},
$$

then the Package-X vertex structure is:

```mathematica
yL \[DoubleStruckCapitalP]L + yR \[DoubleStruckCapitalP]R
```

If the vertex also has a Lorentz-vector structure, such as in some derivative
couplings, write `LTensor[\[Gamma], \[Mu]]` and the chiral projector inside the
same `Spur` / `DiracMatrix` / `FermionLine` chain.

### 4.3 Scalar Propagators

| Particle | Feynman rule | Package-X propagator item |
|------|-------------|--------------------|
| Real scalar `\phi` | `i/(k^2-m_\phi^2)` | `{k, mphi}` |
| Pseudoscalar `A` | Same | `{k, mA}` |
| Charged scalar `H^\[PlusMinus]` | Same | `{k, mHc}` |

### 4.4 Standard Model Higgs Yukawa

In the Standard Model,

$$
\mathcal{L}_{Hff} = - \frac{m_f}{v} H \bar f f .
$$

Therefore the vertex is

$$
-i \frac{m_f}{v} .
$$

Package-X form:

```mathematica
\[DoubleStruckOne]
```

and the overall coupling `m_f / v` is multiplied outside.

### 4.5 Common Yukawa-Sector Calculations

| Calculation object | Recommended function | Notes |
|----------|----------|------|
| Tree diagram `H \to f\bar f` | `Spur` | After spin summation, then contract |
| Scalar-mediated fermion self-energy | `DiracMatrix` | Most natural for off-shell external line |
| Scalar / pseudoscalar one-loop vertex correction | `FermionLine` or `Projector` | Depends on whether external lines are on-shell and whether form factors are needed |
| Fermion loop for `H \to gg` / `H \to \gamma\gamma` | `Spur` | Repository already validates top-quark loop for `H \to gg` |

### 4.6 `\[Gamma]5` And Chiral-Structure Warning

- Any calculation involving `\[Gamma]5` must also read `packagex-reference.md`
  §6
- If the user asks for strict treatment of anomalies, scheme dependence, or
  axial-current renormalization, do not rely only on this file's vertex tables
- Pure tree-level or four-dimensional intermediate results are usually safe;
  one-loop dimensional regularization requires more caution

---

## Cross-Use Recommendations

When the user task falls into the following categories, combine documents this
way:

| User task | Section in this file | Also read |
|----------|----------------|----------------|
| Standard QED / QCD / SM / Yukawa tree diagram | Matching theory section | `packagex-reference.md` §1.4, §1.8 |
| Standard-theory one-loop self-energy / vertex / vacuum polarization | Matching theory section | `packagex-reference.md` §1.1-§1.9 |
| Form-factor projection (such as `g-2`) | Matching theory section | `packagex-reference.md` §1.7 |
| Standard theory + new-physics correction | Read the standard part here first | Then `packagex-reference.md` §5 |
| Tutorial-like code template needed | Matching theory section | `examples/tutorial-examples.md` |
| Minimal electroweak template needed (currently `Wff'` and `\gamma/Z` neutral current are covered) | §3 | `examples/electroweak-minimal-examples.md` §EW1, §EW2 |

This file provides **rules and boundaries**; it does not replace complete
examples. If the user request is very close to one of the five tutorial
examples, prefer directly reusing the matching section of `tutorial-examples.md`.

---

## Appendix: Electroweak Convention Summary

The Standard Model electroweak rules in §3 use the following locally documented
field and sign conventions:

- `A_\[Mu] \bar f f` uses `-i e Q_f \gamma^\mu`
- `Wff'` uses `+i (g/\sqrt{2}) \gamma^\mu P_L`
- `Zff` uses `+i (g/c_W) \gamma^\mu (g_L P_L + g_R P_R)`
- `AWW` uses `-i e`
- `ZWW` uses `+i g c_W`

If the user explicitly specifies another field definition or overall sign
convention, switch the whole convention set before code generation and state the
switch explicitly. Do not flip the sign of only one vertex locally.
