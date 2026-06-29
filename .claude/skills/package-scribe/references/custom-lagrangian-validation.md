# Custom Lagrangian Input Validation (Package-scribe)

This file decides whether a user-provided non-standard-model / BSM Lagrangian
input is sufficient to translate safely into the Package-X code needed for the
current request.

Its responsibility is **translation-readiness validation**, not full model
proof. In other words, it does not promise to validate the complete model the
way FeynRules would. It answers:

- Is the input closed enough, explicit enough, and within current support
  boundaries?
- Can it continue to be translated into the `.wl` script needed for this
  request?
- Where must package-scribe stop and ask the user first?

---

## 1. Scope

Read this file first in these cases:

- The user directly provides a custom interaction Lagrangian
- The user asks to write Package-X code "from this BSM vertex / operator"
- New-physics corrections are layered on top of the Standard Model
- The user asks you to judge whether a Lagrangian input is reasonable before
  continuing the calculation

Usually you do **not** need to read this file first for:

- Pure standard QED / QCD / SM / Yukawa requests
- Standard-theory problems already covered directly by `standard-theories.md`

---

## 2. Verdict Levels

This file recommends three validation verdicts:

### `PASS`

The input is closed enough to continue translation and code generation.

Typical features:

- Field content, Lorentz structure, and coupling notation are explicit
- Mass / propagator information relevant to the current task is complete
- No obvious missing `h.c.`, unclosed indices, or unhandled mixing
- No hard boundary of the current project is triggered

### `PASS_WITH_ASSUMPTIONS`

You may continue, but must first **explicitly state assumptions** to the user.

Typical features:

- The user only provided local interaction terms, not the complete kinetic /
  mass sector
- The current request only needs a local tree-level vertex or local amplitude,
  not complete model propagators
- Some couplings can temporarily be treated under the minimal consistent
  assumptions: real-valued, diagonal flavor, on-shell external lines, etc.

### `BLOCKED`

The current input is insufficient for safe translation; clarification or extra
information is required first.

Typical features:

- The request needs internal propagators or loop diagrams, but mass / spin /
  propagator information is incomplete
- There is an obvious Hermiticity problem or unclosed index
- There are field mixing terms / non-canonical kinetic terms / undiagonalized
  mass matrices, but the user has not provided mass eigenstates or an
  approximation
- The request exceeds current Package-scribe support boundaries

---

## 3. Key Principle: Whether "Full Model Validation" Is Needed Depends On The Task

Do not treat every custom Lagrangian as a complete BSM-model validation problem.

### Cases Where Local Continuation Is Acceptable

If the user only provided a local interaction term and the request is only:

- A tree-level vertex
- A contact-term amplitude
- A local numerator structure under a known propagator

then it is usually unnecessary to demand the full kinetic / mass sector. The
more appropriate verdict is `PASS_WITH_ASSUMPTIONS`, provided that the
interaction term itself is closed under Lorentz / index / Hermiticity checks.

### Cases Where Quadratic Terms And Propagators Must Be Checked

If the request requires:

- Internal propagators for new particles
- One-loop integrals
- Propagators for mixed fields
- Physical amplitudes in the mass-eigenstate basis

then you must check:

- Whether mass parameters are explicit
- Whether spin and propagator form are explicit
- Whether the kinetic term is canonically normalized
- Whether the basis is already a mass-eigenstate / diagonal basis

Otherwise, return `BLOCKED`.

### 3.1 Before Translation, Determine The Requested Result Level

For custom / BSM problems, in addition to checking whether the input is closed,
first determine which result level the user actually wants:

- Bare amplitude / intermediate kernel / Package-X output
- Complete form factor
- `a = (g-2)/2`
- Decay width `\[CapitalGamma]`
- Cross section `\[Sigma]`
- Wilson coefficient / self-energy coefficient

This directly determines:

- Whether current validation is sufficient
- Which couplings, phase-space factors, averaging factors, or normalization maps
  still need to be supplied
- Whether the main user-facing output should be `coreResult` or `finalResult`

If the user does not specify, recommend the default:

- Provide both `coreResult` / `prefactor` / `finalResult` in the code
- In the explanation, treat `finalResult` as the main physical quantity and
  explain how it is restored from `coreResult`

---

## 4. Five Core Checks

## 4.1 Input Closure Check

**What to check**

- The user gave explicit Lagrangian terms, not only a verbal description
- The type of each new field is identifiable: scalar / fermion / vector
- Masses, couplings, flavor indices, and group indices are defined or at least
  named consistently
- It is clear which field a derivative acts on

**Pass standard**

- Every interaction term relevant to the current request can be read uniquely as
  "which fields participate, multiplied by which structure"

**Common blockers**

- "There is a dark photon coupled to fermions" without a concrete term
- The same symbol looks like both a field and a coupling constant
- It is unclear what a derivative term `\partial_\mu` acts on

**Recommended output**

- If only minor information is missing and it does not affect this request:
  `PASS_WITH_ASSUMPTIONS`
- If even the vertex cannot be read uniquely: `BLOCKED`

## 4.2 Hermiticity / `h.c.` Check

**What to check**

- Whether the Lagrangian is explicitly Hermitian
- If couplings may be complex, whether corresponding `h.c.` terms are included
- Whether chiral couplings or flavor-off-diagonal couplings omit conjugate terms

**Pass standard**

- `+ h.c.` is explicit, or
- The term is manifestly Hermitian, or
- The user explicitly says all relevant couplings are real

**Common blockers**

- A non-diagonal term such as `\phi \bar\psi_i P_L \psi_j` has no `h.c.`
- Only half of an interaction is written for complex couplings

**Package-scribe criterion**

- If the minimal consistent assumption "couplings are real" can fix it:
  `PASS_WITH_ASSUMPTIONS`
- If the user clearly needs general complex couplings and `h.c.` is missing:
  `BLOCKED`

## 4.3 Lorentz / Index Closure Check

**What to check**

- Whether each term is a Lorentz scalar
- Whether Lorentz indices are closed in pairs
- Whether spinor, group, and flavor indices are unclosed or ambiguous
- Whether `\gamma^\mu`, `\sigma^{\mu\nu}`, `F_{\mu\nu}`, and derivative indices
  match each other

**Pass standard**

- Every free Lorentz index can be explained as part of an external vector field
  or derivative structure
- There is no obvious missing contraction

**Common blockers**

- `Z_\mu \bar\psi \psi` is missing `\gamma^\mu`
- Index count mismatch such as `F_{\mu\nu}\bar\psi \sigma^\mu \psi`
- A color or flavor index appears only once

**Package-scribe criterion**

- Unclosed indices should usually be `BLOCKED`

## 4.4 Quadratic-Term / Basis / Propagator Readiness Check

**What to check**

- Whether propagators for new particles are needed
- If needed, whether mass terms are explicit
- Whether kinetic terms have canonical normalization
- Whether field mixing, off-diagonal mass terms, or gauge-scalar mixing exist
- Whether the user gave gauge eigenstates or mass eigenstates

**Pass standard**

- This request does not need that information, or
- Propagators and mass eigenstates relevant to this request are explicit enough

**Common blockers**

- The request is a one-loop correction, but the new vector boson has no mass and
  no gauge fixing
- The request includes an internal mixed-scalar propagator, but the mass matrix
  has not been diagonalized
- The kinetic term has an unusual coefficient, and no field rescaling is stated

**Package-scribe criterion**

- Local tree-level vertex: this check can often be downgraded to
  `PASS_WITH_ASSUMPTIONS`
- Internal propagator / loop diagram required: if unclear, usually `BLOCKED`

## 4.5 Package-X Representability And Support-Boundary Check

**What to check**

- Whether the structure can be represented with currently supported objects:
  `Spur`, `DiracMatrix`, `FermionLine`, `Projector`, explicit `LTensor` / `LDot`
- Whether the request enters current high-risk areas:
  - Scheme-sensitive one-loop calculations involving `\[Gamma]5`
  - Majorana fermions
  - Higher-spin fields
  - Undiagonalized mixing
  - Nonminimal ghost / gauge-fixing structures
  - Higher-dimensional derivative operators that create complex
    momentum-dependent vertices

**Pass standard**

- The target structure can be translated directly into Package-X syntax covered
  by current references

**Common blockers**

- The user asks to automate a closed Majorana fermion loop
- The user asks for a complex gauge-scalar-ghost mixing loop in general
  `R_\[Xi]`
- The user asks for a scheme-sensitive one-loop finite part involving
  `\[Gamma]5`

**Package-scribe criterion**

- If it is only a tree diagram or four-dimensional intermediate result, you may
  return `PASS_WITH_ASSUMPTIONS` after stating the boundary
- If it affects a loop finite part or the closed definition of the model, return
  `BLOCKED`

---

## 5. Recommended Output Format

After the user provides a custom Lagrangian, first output a short validation
summary:

```text
Validation verdict: PASS / PASS_WITH_ASSUMPTIONS / BLOCKED

Scope:
- This verdict is only for the current request; it does not mean the full model
  has been validated

Assumptions I will use:
- ...

Warnings:
- ...

Can proceed with:
- ...
```

Then decide whether to enter the translation flow in `packagex-reference.md` §5.

### 5.1 Tone When Using This Template

- For `PASS` or `PASS_WITH_ASSUMPTIONS`, usually **do not** output an additional
  empty `Blocking issues` section; that feels too mechanical
- `Scope` should state whether this verdict applies only to "the current task"
  or is already enough to support full propagator / loop / mass-eigenstate
  analysis
- `Assumptions I will use` should list only assumptions that genuinely affect
  downstream code generation; do not include obvious default filler
- `Warnings` should flag support boundaries, scheme risks, or cases such as
  "tree-level is possible but loop-level is not closed enough"
- `Can proceed with` should directly state what can be done now, not repeat the
  Lagrangian content

### 5.2 If The Verdict Is `BLOCKED`

If the verdict is `BLOCKED`, prefer this structure:

```text
Validation verdict: BLOCKED

Why blocked:
- ...

What I need from you:
- ...
```

In other words, when blocked, you do not need to force the four-section format.
The most important thing is to state the **blocking reason** and the **missing
information needed** clearly.

If what is missing is convention information or quadratic-sector information,
and there is an **obviously reasonable default continuation**, add one more
sentence after `What I need from you`:

```text
Suggested default if you want me to proceed:
- If you have no special preference, I suggest proceeding with {recommended default}; if you agree, I will complete the setup with that choice and generate the code.
```

Examples:

- New massive vector field: suggest "massive vector + Feynman gauge + no mixing"
- Local four-fermion EFT: suggest "local contact term, without assuming a UV
  completion"
- Unspecified flavor: suggest "the simplest consistent diagonal flavor choice"

Do not give a long menu of alternatives here; provide only **one recommended
default**.

---

## 6. Typical Cases

### 6.1 Can Proceed: Local Yukawa Interaction

The user gives:

$$
\mathcal{L} \supset - y \phi \bar\psi \psi
$$

If the request is only:

- Write the tree-level vertex
- Write a one-loop numerator under a known propagator

the usual verdict is `PASS`.

### 6.2 Can Proceed With Assumptions: Local Interaction Without Complete Quadratic Terms

The user gives:

$$
\mathcal{L} \supset - \frac{c}{\Lambda^2}
(\bar\chi \gamma^\mu P_L \ell)(\bar q \gamma_\mu P_L q)
$$

If the request is only a tree-level contact-term amplitude, the usual verdict is
`PASS_WITH_ASSUMPTIONS`:

- Treat it as a local four-fermion operator
- Do not try to reconstruct propagators from a complete UV model

### 6.3 Must Block: Loop Diagram But Propagator Information Is Incomplete

The user gives:

$$
\mathcal{L} \supset g_X X_\mu \bar\psi \gamma^\mu \psi
$$

but asks for a one-loop correction involving `X`, without providing:

- The mass of `X`
- Gauge fixing
- Kinetic term

The verdict should be `BLOCKED`.

### 6.4 Must Block: Obvious Missing `h.c.`

The user gives:

$$
\mathcal{L} \supset - y_{ij} \phi \bar\psi_i P_L \psi_j
$$

and explicitly requests general complex `y_{ij}`. If `+ h.c.` is not written,
the usual verdict is `BLOCKED`.

---

## 7. Division Of Responsibility With Other Documents

- This file handles: **whether the input Lagrangian is sufficient to enter
  automatic translation**
- `packagex-reference.md` §5 handles: **once the input is usable, how to
  translate it into Package-X**
- `standard-theories.md` handles: **standard-theory default rules, not custom
  BSM input**

For custom Lagrangians, the recommended order is always:

1. Read this file first and make a validation verdict
2. If not `BLOCKED`, read `packagex-reference.md` §5
3. Finally organize the output according to the tree / loop flow in `SKILL.md`
