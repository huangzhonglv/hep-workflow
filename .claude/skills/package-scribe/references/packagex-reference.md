# Package-X Reference for Package-scribe

This file is the Package-X API and convention reference used by `SKILL.md`.
Read it by section number as needed.

The content is based on the Package-X 2.0.3 tutorial and official
documentation, adapted for `.wl` script generation.

**Symbol mapping has been validated by exporting from Package-X with
`CharacterEncoding -> "ASCII"`.**

**This file is not a physics-knowledge source for Feynman rules and is not a
complete worked-example collection.** It gives the Package-X syntax and the
minimal practical interpretation of frequent `LoopIntegrate` / `LoopRefine`
outputs. Feynman rules live in `standard-theories.md`; worked examples live in
`tutorial-examples.md`.

---

## §1 Core Function API

### §1.1 LoopIntegrate

**Purpose**: rewrite one-loop integrals as Passarino-Veltman tensor
coefficient functions.

**Signatures**:

```mathematica
LoopIntegrate[numerator, k, {q0, m0}, {q1, m1}, ...]
LoopIntegrate[numerator, k, {q0, m0, w0}, {q1, m1, w1}, ...]
```

**Meaning**:

```text
(i exp(-gamma_E epsilon) / (4 pi)^(d/2))^(-1)
  * mu^(2 epsilon)
  * integral d^d k / (2 pi)^d
  * numerator / [(q0^2 - m0^2)(q1^2 - m1^2)...]
```

**Arguments**:

- `numerator`: a tensor or scalar polynomial in loop momentum `k`. It may
  contain `LDot[k, p]`, `LTensor[k, \[Mu]]`, `Spur`, `DiracMatrix`,
  `FermionLine`, and related Package-X objects.
- `k`: the loop-momentum symbol.
- `{qi, mi}`: a propagator. `qi` is a linear combination of `k` and external
  momenta; `mi` is the mass, not the mass squared.
- `{qi, mi, wi}`: a weighted propagator. The third element is the propagator
  power, such as `{k, 0, 2}` for `1/(k^2)^2`.
- At most four distinct propagators are supported.

**Options**:

| Option | Default | Meaning |
| --- | --- | --- |
| `Cancel` | `Automatic` | Cancel common numerator/denominator factors. `Automatic` disables cancellation when kinematic singularities are detected; `True` forces cancellation; `False` disables it. |
| `Apart` | `False` | Partial-fraction linearly related propagators before covariant decomposition. Set `Apart -> True` only when that step is actually needed. |
| `DiracAlgebra` | `:> $DiracAlgebra` | Whether to perform automatic Dirac algebra on the numerator. With `False`, only linear decomposition is used and the relative order of each gamma product is preserved. Useful when avoiding automatic `\[Gamma]5` or Dirac simplification. |
| `Organization` | `Automatic` | Controls whether results are organized by tensor structure or by PV function. It does not change the mathematical result. |

**Return value**: a Lorentz-covariant linear combination of `PVA`, `PVB`,
`PVC`, and `PVD` coefficient functions. The result is valid for arbitrary
kinematics and all orders in `\[Epsilon]`. Call `LoopRefine` to obtain analytic
expressions suitable for numerical evaluation.

**Key points**:

- For a closed fermion loop, put `Spur[...]` directly in the first argument.
- For open fermion lines, use `DiracMatrix[...]` or `FermionLine[...]`.
- If the numerator contains Dirac algebra for open fermion lines,
  `LoopIntegrate` internally calls `FermionLineExpand`.
- Propagator masses are masses, not squared masses: use `{k + p, me}`, not
  `{k + p, me^2}`.
- Use the third propagator element for higher poles. `{k, 0, 2}` means
  `(k^2)^(-2)`. Do not write duplicate propagators such as
  `{k, 0}, {k, 0}`; Package-X treats them as distinct propagators.
- When projection or special kinematics can create removable singularities,
  keeping `Cancel -> Automatic` is usually safer than manually overriding it.
- `Cancel -> False` is legal but often produces bulkier PV expressions. Do
  not set it unless the uncancelled covariant decomposition is explicitly
  required.
- Additional simplification among linearly related propagators usually needs
  `Apart -> True`.

### §1.2 LoopRefine

**Purpose**: replace PV coefficient functions by analytic expressions and take
the `d -> 4` limit safely. It is also used for tree-level expressions when a
safe `d -> 4` reduction is needed.

**Signature**:

```mathematica
LoopRefine[expr]
```

**Options**:

| Option | Default | Meaning |
| --- | --- | --- |
| `Analytic` | `False` | Allows analytic continuation of `\[Epsilon]` to large negative values when non-logarithmic, power-like IR singularities occur at physical thresholds. This is an advanced recovery option. |
| `Part` | `All` | Selects a piece of the result: `UVDivergent`, `IRDivergent`, or `Finite`. |
| `ExplicitC0` | `Automatic` | Controls whether explicit analytic forms for `ScalarC0` are inserted. `None` keeps `ScalarC0` unexpanded and can be more numerically stable; `All` expands more aggressively. |
| `TargetScale` | `Automatic` | Controls the reference mass scale used in logarithms. Keep `Automatic` unless a common reference scale is needed. |
| `Organization` | `Function` | Controls organization by tensor structure, by special function, or with no extra reorganization. In the local Package-X installation, `Options[LoopRefine]` returns `Organization -> Function`. |

**Common display forms**:

- UV divergences appear as `1/\[Epsilon]` poles.
- IR divergences appear as `1/\[Epsilon]` or `1/\[Epsilon]^2` poles.
- Typeset output often displays the 't Hooft scale as decorated `mu` and the
  dimension parameter as `d`.
- In generated `.wl` scripts the only standard input symbols are
  `\[Micro]` for the 't Hooft scale and `\[ScriptD]` for the dimension
  parameter.
- Special functions include `DiscB`, `ScalarC0`, and `ScalarD0`.

**Key points**:

- `LoopRefine` converts `PVA/PVB/PVC/PVD` to analytic expressions built from
  elementary functions and Package-X special functions.
- Results are valid for real external invariants, positive or negative, and
  nonnegative internal masses.
- `LoopRefine` replaces `\[ScriptD]` by `4 - 2 \[Epsilon]` and keeps terms
  through `O(\[Epsilon]^0)`.
- If the final result no longer contains `\[Epsilon]`, it has no explicit
  logarithmic UV or IR pole.
- Numerical evaluation implements the correct `+i epsilon` prescription.
- At physical thresholds or power-like IR singularities, ordinary
  `LoopRefine` may emit `LoopRefine::sing` and return `ComplexInfinity`. Move
  away from the singular point and take a limit, or carefully try
  `Analytic -> True`.
- `TargetScale -> M` changes only the way logarithms are written; it does not
  change the physical result.
- `TargetScale` should be a real positive mass scale. Using an external
  invariant as `TargetScale` is reliable only under the corresponding Euclidean
  assumptions.
- Some official notebooks list `Organization -> LTensor`; the local
  installation returns `Organization -> Function`. Package-scribe follows the
  observed local behavior.
- Use `LoopRefine` for tree diagrams that need the `d -> 4` limit. Do not
  manually replace a dimension symbol by `4`.
- In `.wl` scripts, never write `\[ScriptD] -> 4`. Seeing `d` in typeset
  output is not permission to write `d -> 4`.
- `LoopRefine` has no `FinalSubstitutions` option. Apply kinematic
  substitutions explicitly with `LoopRefine[expr /. rules]` or
  `(LoopRefine[expr] /. rules)`.

### §1.3 LoopRefineSeries

**Purpose**: Taylor-expand `LoopIntegrate` output, often to avoid numerical
instability in a full analytic expression.

**Signature**:

```mathematica
LoopRefineSeries[expr, {var1, point1, order1}, {var2, point2, order2}, ...]
```

**Options**:

| Option | Default | Meaning |
| --- | --- | --- |
| `Analytic` | `False` | Allows analytic continuation in `\[Epsilon]` near Landau, power, or threshold singularities. |
| `Part` | `All` | Same as `LoopRefine`: keep all, UV-only, IR-only, or finite-only pieces. |
| `ExplicitC0` | `Automatic` | Controls whether `ScalarC0` in expansion coefficients is made explicit. |
| `TargetScale` | `Automatic` | Controls the reference scale used in logarithms. |
| `Organization` | `Function` | Controls result organization. The local default is also `Function`. |

**Example**:

```mathematica
(* Expand around s = 0 and t = 0 through first order. *)
approx = LoopRefineSeries[integral, {s, 0, 1}, {t, 0, 1}] // Normal
```

**Use cases**:

- Large scale separations make the full expression numerically unstable.
- A specific external-momentum limit is needed, such as `q^2 -> 0` for an
  anomalous magnetic moment.
- A heavy-mass expansion is needed, such as `m -> Infinity`.

**Key points**:

- `LoopRefineSeries` computes the necessary derivatives of PV coefficient
  functions and calls an internal form of `LoopRefine`.
- It cannot construct an ordinary series near Landau singularities, normal
  thresholds, or singular points where internal masses vanish.
- If such an expansion is still required, `Analytic -> True` may be attempted
  as an advanced option.
- Multivariate expansion is sequential: the first variable is expanded first,
  then the second, and so on.

### §1.4 Spur

**Purpose**: compute Dirac traces.

**Signature**:

```mathematica
Spur[expr1, expr2, ...]
```

There are no user-level options.

**Syntax rules**:

- Comma-separated arguments represent ordered matrix multiplication.
- Addition may appear inside one argument.
- Scalars in spinor space must multiply `\[DoubleStruckOne]` before being
  added to a Dirac matrix.
- A Feynman slash is written with a Lorentz dot product:
  `LDot[p, \[Gamma]]`.
- Put `Projector[...]` in its own argument when using a form-factor projector.

**Example**:

```mathematica
Spur[
  LDot[k, \[Gamma]] + m \[DoubleStruckOne],
  LTensor[\[Gamma], \[Mu]],
  LDot[k + p, \[Gamma]] + m \[DoubleStruckOne],
  LTensor[\[Gamma], \[Nu]]
]
```

**Inside `LoopIntegrate`**:

```mathematica
LoopIntegrate[
  Spur[
    LTensor[\[Gamma], \[Nu]],
    LDot[k, \[Gamma]] + m \[DoubleStruckOne],
    LTensor[\[Gamma], \[Mu]],
    LDot[k - q, \[Gamma]] + m \[DoubleStruckOne]
  ],
  k,
  {k, m},
  {k - q, m}
]
```

**Key points**:

- `Spur` works in `d = 4 - 2 \[Epsilon]` dimensions by default.
- Use `LoopRefine` for a safe `d -> 4` limit; do not substitute the dimension
  by hand.
- The trace can include `Projector[...]` for extracting form factors.

### §1.5 DiracMatrix

**Purpose**: represent an open off-shell fermion line without taking a trace.

**Signature**:

```mathematica
DiracMatrix[expr1, expr2, ...]
```

`DiracMatrix[]` is the spinor-space identity.

**Rules**:

- The syntax is the same ordered comma syntax used by `Spur`.
- Scalars that are added to matrices must multiply `\[DoubleStruckOne]`.
- `FermionLineExpand` can expand and collect open-line objects.

**`FermionLineExpand` restrictions**:

- The expression must contain only one of these object families:
  `DiracMatrix`, `FermionLine`, or `FermionLineProduct`.
- It must be strictly linear in those objects.
- Mixing object families or wrapping them nonlinearly can give incorrect
  results.
- `LoopIntegrate` calls the expansion internally when needed.

**Example with a covariant-gauge photon propagator**:

```mathematica
num = DiracMatrix[
  LTensor[\[Gamma], \[Nu]],
  LDot[p - k, \[Gamma]] + m \[DoubleStruckOne],
  LTensor[\[Gamma], \[Mu]]
] (LTensor[g, \[Mu], \[Nu]] LDot[k, k]
   - (1 - \[Xi]) LTensor[k, \[Mu]] LTensor[k, \[Nu]]) // Contract;

int = LoopIntegrate[num, k, {k - p, m}, {k, 0, 2}];
result = LoopRefine[int];
```

### §1.6 FermionLine

**Purpose**: represent on-shell external fermions and allow Package-X to use
equations of motion and Gordon identities.

**Signature**:

```mathematica
FermionLine[{sgn2, p2, m2}, {sgn1, p1, m1}, DiracMatrix[...]]
```

`sgn = 1` denotes a `u` spinor and `sgn = -1` denotes a `v` spinor. The first
spinor specification is the barred spinor, and the second is the unbarred
spinor.

**Example**:

```mathematica
FermionLine[
  {1, p2, m},
  {1, p1, m},
  DiracMatrix[LTensor[\[Gamma], \[Mu]]]
]
```

**Products** of multiple fermion lines become `FermionLineProduct` objects.

**Effects of `FermionLineExpand`**:

- Expands sums inside spinor chains.
- Applies on-shell equations of motion.
- Applies Gordon identities where possible.
- Preserves strict linearity requirements.

### §1.7 Projector

**Purpose**: extract form factors from Dirac traces or on-shell fermion
vertices.

**Signatures**:

```mathematica
Projector["name"][{p, m}]
Projector["name"][{p1, m1}, {p2, m2}]
Projector["F2", \[Mu]][{p1, m}, {p2, m}]
```

There are no user-level options.

**Families**:

1. Self-energy projectors.
2. Scalar and pseudoscalar density projectors.
3. Vector and axial-vector vertex projectors.

#### Self-energy projectors

For a fermion self-energy, Package-X uses the standard decomposition into
kinetic, mass, axial-kinetic, and imaginary-mass structures.

Common names:

| Name | Meaning |
| --- | --- |
| `"Kinetic"` / `"A"` | coefficient of the kinetic slash structure |
| `"Mass"` / `"B"` | mass coefficient |
| `"AxialKinetic"` / `"C"` | axial kinetic coefficient |
| `"ImaginaryMass"` / `"E"` | imaginary mass coefficient |
| `"AL"`, `"BL"`, `"AR"`, `"BR"` | chiral variants |

Check the chosen mass normalization before interpreting a mass coefficient as
a physical mass correction.

#### Scalar and pseudoscalar density projectors

Common names:

| Name | Meaning |
| --- | --- |
| `"Scalar"` / `"S"` | scalar form factor |
| `"Pseudoscalar"` / `"P"` | pseudoscalar form factor |

#### Vector and axial-vector vertex projectors

Package-X uses the convention `q = p2 - p1` for the momentum transfer.

Common names:

| Name | Meaning |
| --- | --- |
| `"F1"` / `"Dirac"` | Dirac form factor |
| `"F2"` / `"Pauli"` | Pauli form factor |
| `"F3"` | additional vector structure |
| `"G1"` / `"Anapole"` | anapole form factor |
| `"G2"` / `"EDM"` | electric dipole form factor |
| `"G3"` | additional axial structure |
| `"AL"`, `"BL"`, `"CL"`, `"AR"`, `"BR"`, `"CR"` | chiral variants |
| `"SachsElectric"` / `"SachsMagnetic"` | equal-mass Sachs form factors |

**Usage pattern**:

```mathematica
numerator = Contract[
  Spur[
    LTensor[\[Gamma], \[Rho]],
    LDot[p2 - k, \[Gamma]] + m \[DoubleStruckOne],
    LTensor[\[Gamma], \[Mu]],
    LDot[p1 - k, \[Gamma]] + m \[DoubleStruckOne],
    LTensor[\[Gamma], \[Rho]],
    Projector["F2", \[Mu]][{p1, m}, {p2, m}]
  ]
]
```

Put the `Projector[...]` object in a separate `Spur` slot. Do not multiply it
into a scalar prefactor or hide it inside another expression.

**Momentum-transfer convention**:

- Package-X's standard projector convention is space-like, with
  `q = p2 - p1`.
- For time-like decays, rewrite the kinematics so the projector sees the
  appropriate sign convention, or clearly document the sign map in
  `prefactor` / `normalizationMap`.

**Removable singularities**:

Projectors may introduce kinematic singularities that cancel in the final
expression.

Recommended handling:

1. Call `LoopIntegrate` normally and keep the default `Cancel -> Automatic`.
2. For on-shell vertex form factors, `LoopIntegrate` often detects
   `Projector` and applies the relevant on-shell external-spinor conditions.
3. Use `Simplify`, `Cancel`, or `Factor` before substituting singular
   kinematic limits such as `q2 -> 0`.
4. If `Power::infy` appears after a direct substitution, do not treat it as a
   physical divergence until the removable singularity has been simplified.
5. When a direct limit is unstable, use `LoopRefineSeries`.

### §1.8 Helper Functions

#### Contract

**Purpose**: contract repeated Lorentz indices in tensor expressions.

```mathematica
Contract[expr]
```

Important constraints:

- Each Lorentz index may appear at most twice.
- `Contract` does not enter `DiracMatrix` or `FermionLine` internals; simplify
  those structures with their own tools first.
- Avoid using `g` or `d` as ordinary variables because Package-X reserves them
  for the metric tensor and dimension.

#### Longitudinal and Transverse

**Purpose**: project rank-2 tensors into longitudinal and transverse scalar
components.

```mathematica
Longitudinal[expr, p, \[Mu], \[Nu]]
Transverse[expr, p, \[Mu], \[Nu]]
```

Use these before `LoopRefine` when extracting self-energy components from
rank-2 tensor structures.

#### DiscExpand

**Purpose**: expand branch-cut discontinuity helper functions.

```mathematica
DiscExpand[expr]
```

It applies to `DiscB`. It does not expand `ScalarC0` or `ScalarD0`.

#### MandelstamRelations

**Purpose**: generate replacement rules for `2 -> 2` kinematics.

```mathematica
MandelstamRelations[...]
```

Use it only for standard `2 -> 2` Mandelstam relations. It is not a general
on-shell simplifier. Check the `Eliminate` option before relying on which
variable is removed.

### §1.9 Common Output Objects

**Purpose**: identify frequent PV coefficient functions and special functions
in `LoopIntegrate` / `LoopRefine` output.

| Object | Meaning |
| --- | --- |
| `PVA`, `PVB`, `PVC`, `PVD` | Passarino-Veltman coefficient functions produced by `LoopIntegrate`. `A/B/C/D` correspond to one-, two-, three-, and four-propagator integrals. |
| `DiscB` | Branch-cut discontinuity helper for two-point functions. |
| `ScalarC0` | Scalar three-point function. |
| `ScalarD0` | Scalar four-point function. |
| `C0Expand` / `D0Expand` | Utilities for expanding scalar three- and four-point functions where supported. |

`LoopRefine` normally eliminates `PVA/PVB/PVC/PVD`. Depending on options and
kinematics, it may keep `ScalarC0`, `ScalarD0`, or discontinuity helpers.

### §1.10 Option Scan Summary

In the local Package-X installation, these functions have user-level options:

- `LoopIntegrate`
- `LoopRefine`
- `LoopRefineSeries`
- `MandelstamRelations`

Functions such as `Spur`, `DiracMatrix`, `FermionLine`, `Projector`,
`Contract`, `Longitudinal`, `Transverse`, and `DiscExpand` are used without
ordinary user-level options in Package-scribe outputs.

---

## §2 Symbol Mapping Table

Use `.wl` input forms, not notebook display forms. The table below records the
script-safe spelling used by generated Package-scribe code.

| Concept | `.wl` spelling to generate | Notes |
| --- | --- | --- |
| Spinor identity | `\[DoubleStruckOne]` | Required when adding a scalar to a Dirac matrix. |
| Dirac gamma matrix | `LTensor[\[Gamma], \[Mu]]` | Use `\[Gamma]` as the tensor head; do not use `\[CapitalGamma]`. |
| Dirac sigma matrix | `LTensor[\[Sigma], \[Mu], \[Nu]]` | Package-X convention: `(I/2)[gamma^mu, gamma^nu]`. |
| Gamma5 | `\[Gamma]5` | See §6 before using it in scheme-sensitive loop work. |
| Left projector | `\[DoubleStruckCapitalP]L` | Already a spinor-space object. |
| Right projector | `\[DoubleStruckCapitalP]R` | Already a spinor-space object. |
| Metric tensor | `LTensor[g, \[Mu], \[Nu]]` | Do not reuse `g` as a coupling variable. |
| Levi-Civita tensor | `LTensor[\[CurlyEpsilon], \[Mu], \[Nu], \[Rho], \[Sigma]]` | Reserve `\[Epsilon]` for the dimensional regulator. |
| Dimension parameter | `\[ScriptD]` | `LoopRefine` replaces it with `4 - 2 \[Epsilon]`. |
| 't Hooft scale | `\[Micro]` | Script input standard. |
| Dimensional regulator | `\[Epsilon]` | Used in poles. |
| Lorentz indices | `\[Mu]`, `\[Nu]`, `\[Rho]`, `\[Sigma]`, ... | Use ordinary symbols; avoid formatted subscripts. |

Do not copy typeset notebook glyphs into `.wl` files unless they are one of
the script spellings above.

---

## §3 Input Conventions

### §3.1 `LDot`

`LDot` represents Lorentz dot products.

```mathematica
LDot[p, q]              (* p.q in Lorentz metric *)
LDot[p, \[Gamma]]       (* p slash *)
LDot[p + q, r]          (* linear combinations are allowed *)
```

In `.wl` scripts, `p.k` is Mathematica's ordinary dot product, not a Lorentz
dot product. Always use `LDot[p, k]` for Lorentz products.

`LDot` is symmetric and linear for vector arguments.

### §3.2 `LTensor`

`LTensor` represents Lorentz tensors.

```mathematica
LTensor[p, \[Mu]]
LTensor[\[Gamma], \[Mu]]
LTensor[\[Sigma], \[Mu], \[Nu]]
LTensor[g, \[Mu], \[Nu]]
LTensor[\[CurlyEpsilon], \[Mu], \[Nu], \[Rho], \[Sigma]]
```

The first argument is the tensor head. Lorentz indices should be plain symbols.
Package-X may reorder built-in tensor structures into its canonical order.

### §3.3 Feynman Slash

A Feynman slash is written as a Lorentz dot product with `\[Gamma]`:

```mathematica
LDot[p, \[Gamma]]
LDot[p + q, \[Gamma]]
```

### §3.4 Input Rules Inside `Spur`, `DiracMatrix`, and `FermionLine`

**Comma rule**: ordered matrix multiplication is represented by separate
arguments.

```mathematica
Spur[LTensor[\[Gamma], \[Mu]], LTensor[\[Gamma], \[Nu]]]
```

**Addition rule**: spinor-space addition belongs inside one argument.

```mathematica
Spur[
  LDot[p, \[Gamma]] + m \[DoubleStruckOne],
  LTensor[\[Gamma], \[Mu]]
]
```

**`\[DoubleStruckOne]` rule**: scalar constants such as masses and couplings
are not spinor-space matrices. To add them to a Dirac matrix, multiply them by
the identity Dirac matrix.

```mathematica
(* Correct: m times the spinor identity. *)
LDot[p, \[Gamma]] + m \[DoubleStruckOne]

(* Incorrect: m is a scalar, not a matrix. *)
LDot[p, \[Gamma]] + m
```

This rule applies to all arguments of `Spur`, `DiracMatrix`, and
`FermionLine`.

`\[DoubleStruckCapitalP]L` and `\[DoubleStruckCapitalP]R` are already
spinor-space objects; do not multiply them by `\[DoubleStruckOne]`.

### §3.5 Propagator Format

`LoopIntegrate` propagators use these forms:

| Format | Meaning | Example |
| --- | --- | --- |
| `{q, m}` | one factor of `1/(q^2 - m^2)` | `{k + p, me}` |
| `{q, m, w}` | power `w` of the same propagator | `{k, 0, 2}` |

Key rules:

- The mass argument is a mass, not a mass squared.
- The optional third element is the propagator power.
- Repeated propagators should be represented by the third element, not by
  duplicate propagator entries.

### §3.6 On-Shell Conditions

On-shell conditions are ordinary Mathematica replacement rules:

```mathematica
onShell = {
  LDot[p, p] -> m^2,
  LDot[q, q] -> q2,
  LDot[p, q] -> pq
};

exprOnShell = expr /. onShell;
```

For on-shell vector bosons, transversality can also be applied with explicit
rules appropriate to the tensor basis:

```mathematica
(* p.epsilon(p) = 0; remove external-momentum components carried by the polarization index. *)
```

Do not use broad pattern rules that may also remove loop-momentum dependence.

---

## §4 Normalization and Result Layers

### §4.1 Factor Omitted by `LoopIntegrate` / `LoopRefine`

`LoopIntegrate` and `LoopRefine` output omits the universal one-loop
normalization factor

```text
i exp(-gamma_E epsilon) / (4 pi)^(d/2)
```

relative to the physical loop integral convention.

Practical consequences:

- The explicit overall factor `i/(16 pi^2)` is omitted.
- The `1/epsilon` poles are not paired with `-gamma_E + log(4 pi)`. In the
  MS-bar scheme, those constants are absorbed with the pole into
  counterterms.
- The 't Hooft scale may appear in typeset output with a decorated `mu`, but
  generated `.wl` code should use `\[Micro]`.

### §4.2 Result Layers and Prefactor Restoration

Package-scribe generated code should normally separate results into three
layers:

1. `coreResult`
   - The direct Package-X output, or the first Lorentz / Dirac / PV structure
     obtained in the `.wl` file.
   - For loop diagrams, this usually does not yet include explicit couplings,
     `i/(16 \[Pi]^2)`, color factors, or symmetry factors.
   - For tree diagrams, this usually does not yet include couplings, color
     factors, spin averages, or phase space.
2. `prefactor`
   - The map needed to convert `coreResult` into the requested physical
     quantity.
   - It may include signs and form-factor normalization maps, not just a
     positive multiplicative coefficient.
   - Common contents include couplings, `i/(16 \[Pi]^2)`, the closed-fermion
     loop factor `-1`, color factors, symmetry factors, initial-state
     averages, phase space, and sign / normalization conventions.
3. `finalResult`
   - The physical quantity the user asked for: a decay width, cross section,
     complete form factor, `a = (g - 2)/2`, self-energy coefficient, and so on.

Default output policy:

- Unless the user explicitly asks otherwise, generate all three names:
  `coreResult`, `prefactor`, and `finalResult`.
- If the user asks only for Package-X output, an intermediate structure, or a
  tutorial-comparison result, `coreResult` may be the main output, but still
  document how to restore `finalResult`.
- If the user asks only for the final physical quantity, present `finalResult`
  as the main answer while keeping the three-layer naming in code.

Recommended code shape:

```mathematica
coreResult = LoopRefine[...];
prefactor = ...;
finalResult = Simplify[prefactor * coreResult];
```

### §4.3 Restoring the Full Physical Result

For a loop amplitude, the schematic physical result is

```text
i M_loop =
  (couplings) * (symmetry factor) * (color factor)
  * i/(16 pi^2)
  * [LoopRefine output]
```

In the MS-bar scheme, counterterms that absorb a `1/epsilon` pole must be
paired with `-gamma_E + log(4 pi)`.

Tree-level expressions are not affected by the omitted one-loop normalization.
Tree diagrams use `LoopRefine` only when a safe `d -> 4` reduction is needed.

### §4.4 Common Prefactor Checklist

- Explicit couplings, such as `e^2`, `gs^2`, or `y^2`.
- Universal one-loop factor `i/(16 \[Pi]^2)`.
- Closed fermion loop factor `-1`.
- Color and symmetry factors, such as `Nc`, `CF`, `TF`, or `1/2`.
- Initial-state spin and color averages for cross sections.
- Phase-space and flux factors when converting `|M|^2` into a width or cross
  section.
- Form-factor normalization maps, including possible overall signs or
  convention-dependent factors.

### §4.5 Projector / g-2 Result Mapping

`Projector["F2", \[Mu]]` most directly returns a `coreResult` close to the
Package-X Pauli form-factor coefficient. The physical Pauli form factor and
the anomalous magnetic moment still depend on the full Feynman-rule prefactor
and any sign convention.

Typical pattern:

```mathematica
coreResult = ...;                 (* Package-X F2-like coefficient *)
normalizationMap = ...;           (* sign or convention map if needed *)
physicalF2 = normalizationMap * prefactor * coreResult;
a = physicalF2 /. q2 -> 0;
```

Common cases:

- QED one-loop `g-2`: `coreResult` is interpreted as `F2(0)` after the QED
  prefactor is restored, giving the standard `alpha/(2 Pi)` result.
- Custom BSM scalar Yukawa `g-2`: if the vertex convention gives
  `coreResult = -F2_phys(0)`, encode that sign in `prefactor` or
  `normalizationMap`.

Core principle: do not report only `coreResult` and leave the user to guess
which overall factors are still missing.

---

## §5 Translating Custom Theories

When the user provides a custom Lagrangian outside the standard QED/QCD/SM
cases, manually derive the Feynman rules and translate them into Package-X
input.

**Default prerequisite**: read `custom-lagrangian-validation.md` first, and
continue only if the current validation verdict is not `BLOCKED`. Do not jump
directly into Package-X translation when the input itself has not passed the
validation boundary.

### Steps

1. Confirm which information the request actually needs.
   - For a local tree-level vertex or contact operator, a complete kinetic and
     mass sector may not be required.
   - For internal propagators or loop diagrams, mass, spin, propagator, and
     basis information must be explicit.
2. Identify interaction vertices by expanding the Lagrangian and collecting
   all three-leg and higher interaction terms.
3. Extract vertex factors: participating fields, Lorentz structure, chirality
   structure, and couplings.
4. Determine propagators:
   - Scalar: `1/(k^2 - m^2)` -> `{k, m}`.
   - Fermion: `(k slash + m)/(k^2 - m^2)` -> numerator
     `LDot[k, \[Gamma]] + m \[DoubleStruckOne]`, propagator `{k, m}`.
   - Vector in Feynman gauge: `-g^{mu nu}/(k^2 - m^2)` -> numerator contains
     `LTensor[g, \[Mu], \[Nu]]`, propagator `{k, m}`.
   - Vector in a general covariant gauge: higher poles may require forms such
     as `{k, m, 2}`.
5. Translate the result into Package-X input.

If the user gives only a local interaction term and the current request does
not need the full propagator sector, proceed after explicitly stating the
assumption. Do not invent a complete UV model merely to evaluate a local
tree-level operator.

### Example: Scalar-Fermion Yukawa Coupling

Lagrangian term:

```text
L contains - y phi psiBar psi
```

Vertex factor:

```text
-i y
```

For a one-loop scalar self-energy with a fermion loop:

```mathematica
(-I y)^2 LoopIntegrate[
  Spur[
    LDot[k, \[Gamma]] + mf \[DoubleStruckOne],
    LDot[k - p, \[Gamma]] + mf \[DoubleStruckOne]
  ],
  k,
  {k, mf},
  {k - p, mf}
]
```

The closed-fermion-loop sign and other overall factors may be kept separately
in `prefactor`; keep the split clear.

### Chiral Coupling

Lagrangian term:

```text
L contains - psiBar (gL PL + gR PR) psi Z_mu
```

Package-X structure:

```mathematica
vertex =
  gL \[DoubleStruckCapitalP]L + gR \[DoubleStruckCapitalP]R;

chain =
  Spur[
    vertex,
    LTensor[\[Gamma], \[Mu]],
    LDot[k, \[Gamma]] + m \[DoubleStruckOne]
  ];
```

Chiral projectors are already spinor-space objects and do not need
`\[DoubleStruckOne]`.

---

## §6 `\[Gamma]5` Issues and Workarounds

### Problem

In Package-X, `\[Gamma]5` is defined to anticommute with all Dirac matrices in
`d = 4 - 2 \[Epsilon]` dimensions. This definition can be algebraically
inconsistent at `O(\[Epsilon])`, which means the finite part of an integral
with `1/\[Epsilon]` poles may be wrong.

### Affected Calculations

- Triangle diagrams with a closed fermion loop and two or three chiral-vector
  vertices, such as AVV or AAA triangles.
- Box diagrams involving fermions and internal gauge bosons in unitary gauge,
  where intermediate UV-divergent structures appear.

### Usually Unaffected Calculations

- Bubble and tadpole diagrams without anomalous `\[Gamma]5` sensitivity.
- Calculations without `\[Gamma]5`.

### Recommended Workarounds

For triangle diagrams, use the Adler prescription: fix the finite part by
enforcing the relevant Ward identities.

For box diagrams, use a renormalizable gauge such as 't Hooft-Feynman gauge
instead of unitary gauge. Landau gauge (`xi = 0`) can reintroduce problems
through mass-singular `1/\[Epsilon]` poles.

For maximum control:

```mathematica
$DiracAlgebra = False;
```

This disables automatic Dirac-algebra simplification so the gamma5 handling
can be controlled manually. After use, run:

```mathematica
Clear[$DiracAlgebra]
```

---

## §7 Common Pitfalls

### §7.1 Formatted Variable Names

Never use subscripted variables as symbols. Package-X can parse subscripts as
`LTensor` components:

```mathematica
Subscript[m, H]
```

may be interpreted as a component of a four-vector rather than a scalar Higgs
mass, leading `Contract` to perform incorrect index contractions.

Use ordinary symbol names such as `mH`, `me`, and `mZ`.

Do not use Mathematica's prime syntax `p'`; it parses as `Derivative[1][p]`.
Use `p2` or `pp` instead.

### §7.2 Removable Singularities After `Projector`

See §1.7. The essential points are:

- Kinematic singularities introduced by a projector can be removable.
- Simplify with `Simplify`, `Cancel`, or `Factor` before substituting a
  singular kinematic limit.
- A direct substitution can produce `Power::infy` even when the physical form
  factor is finite.

### §7.3 Vanishing Gram Determinant

When the external-momentum Gram determinant vanishes, PV decomposition can
become singular. Possible remedies:

- Apply on-shell conditions before reaching the singular kinematic point.
- Use `LoopRefineSeries`.
- Revisit `Cancel` and `Apart` options in `LoopIntegrate`.

### §7.4 Numerical Instability

Large scale separation, such as `m >> Sqrt[s]`, can make machine-precision
evaluation unstable.

Remedies:

```mathematica
N[expr /. numericRules, 50]
```

or use an analytic expansion:

```mathematica
LoopRefineSeries[expr, {s, 0, 2}]
```

or keep special functions unexpanded:

```mathematica
LoopRefine[expr, ExplicitC0 -> None]
```

### §7.5 `FermionLineExpand` Input Restrictions

`FermionLineExpand[expr]` is not a universal Dirac-algebra expander.

- `expr` must contain only one object family among `DiracMatrix`,
  `FermionLine`, and `FermionLineProduct`.
- The expression must be strictly linear in that object family.
- Mixing object families or applying nonlinear wrappers can give incorrect
  results.

### §7.6 The `.` Operator in `.wl`

`p.k` in a `.wl` script is not a Lorentz dot product. Use `LDot[p, k]`; see
§3.1.

### §7.7 Missing `\[DoubleStruckOne]`

Scalars in `Spur`, `DiracMatrix`, and `FermionLine` must multiply
`\[DoubleStruckOne]` before they are added to Dirac matrices. Missing the
identity may not cause a hard error, but it can give a wrong trace.

### §7.8 `g` and `d` as Variable Names

Package-X uses ordinary letters `g` for the metric tensor and `d` for the
dimension. Avoid using `g` or `d` as unrelated variables, such as a coupling
constant or an independent dimension parameter. Use names such as `gc` or
`dim` instead.

---

## Spacetime Convention

| Quantity | Convention |
| --- | --- |
| Metric signature | `g^{mu nu} = diag(+, -, -, -)` |
| Spacetime dimension | `\[ScriptD] = 4 - 2 \[Epsilon]` |
| Dirac sigma matrix | `sigma^{mu nu} = (i/2)[gamma^mu, gamma^nu]` |
| Gamma5 | `gamma5 = i gamma^0 gamma^1 gamma^2 gamma^3` |
| Chiral projectors | `PL = (1 - gamma5)/2`, `PR = (1 + gamma5)/2` |
| Levi-Civita symbol | `epsilon^{0123} = +1` |
