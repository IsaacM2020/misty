"""Words to hand Whisper before it reads a class, so a Physics class says "Doppler" and not "Doppler's a".

Whisper's `initial_prompt` biases spelling toward whatever it is shown. The prompt is the subject's own terms (IB
syllabus words) plus the `wanted` side of his `~/Misty/dictionary.txt` (`heard => wanted`, the same file dictation uses).
It is short on purpose: a long prompt makes Whisper repeat it on quiet audio. Local only, nothing leaves the Mac.
"""
from __future__ import annotations

from pathlib import Path

MAX_CHARS = 600                   # about 150 tokens; Whisper reads the last 224 of a prompt

TERMS = {
    "Physics": "SI units, vector, scalar, displacement, acceleration, kinematics, projectile, Newton's laws, momentum, "
               "impulse, kinetic energy, gravitational potential energy, work done, power, efficiency, SHM, simple "
               "harmonic motion, amplitude, wavelength, frequency, Doppler effect, superposition, diffraction, "
               "interference, polarization, refractive index, Snell's law, electric field, potential difference, "
               "resistivity, internal resistance, magnetic flux, Faraday's law, centripetal force, Kepler, "
               "radioactive decay, half-life, binding energy, photoelectric effect, de Broglie, uncertainty",
    "Chem": "mole, stoichiometry, empirical formula, Avogadro, ideal gas, enthalpy, Hess's law, bond enthalpy, entropy, "
            "Gibbs free energy, Le Chatelier, equilibrium constant, Kc, activation energy, rate-determining step, "
            "Arrhenius, electronegativity, hybridisation, sigma and pi bonds, delocalisation, ligand, oxidation state, "
            "redox, electrode potential, electrolysis, nucleophile, electrophile, SN1, SN2, ester, carboxylic acid, "
            "buffer, pH, pKa, titration, spectroscopy, NMR",
    "Math": "derivative, integral, logarithm, exponential, polynomial, binomial expansion, sequences and series, "
            "arithmetic, geometric, complex numbers, modulus, argument, vectors, dot product, cross product, "
            "probability, Bayes, normal distribution, standard deviation, hypothesis test, limit, Maclaurin, "
            "differential equation, trigonometric identity, radian, sinusoidal, asymptote, inverse function",
    "Econ": "opportunity cost, scarcity, price elasticity of demand, cross elasticity, consumer surplus, producer "
            "surplus, deadweight loss, externality, public good, market failure, oligopoly, monopolistic competition, "
            "Gini coefficient, Lorenz curve, aggregate demand, aggregate supply, fiscal policy, monetary policy, "
            "inflation, unemployment, GDP, balance of payments, exchange rate, terms of trade, comparative advantage",
    "English": "rhetoric, ethos, pathos, logos, semiotics, discourse, register, connotation, juxtaposition, "
               "intertextuality, narrative voice, syntax, tone, audience, purpose, context, paper 1, paper 2",
    "Spanish": "pretérito, imperfecto, subjuntivo, condicional, ser y estar, por y para, vocabulario, redacción",
}


def wanted_words(home: Path) -> list[str]:
    """The `wanted` side of `~/Misty/dictionary.txt`, in file order. No file, no extras."""
    try:
        text = (home / "dictionary.txt").read_text(encoding="utf-8")
    except (OSError, ValueError):             # unreadable, or not UTF-8: no extras, never a crash
        return []
    out = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("=>")]
        if len(parts) == 2 and parts[0] and parts[1] and not parts[0].startswith("#") and parts[1] not in out:
            out.append(parts[1])
    return out


def prompt_for(subject: str, home: Path) -> str | None:
    """The Whisper prompt for a class: his own words first (they win the space), then the subject's terms."""
    mine = wanted_words(home)
    terms = TERMS.get(subject, "")
    if not mine and not terms:
        return None
    text = f"A {subject} class. " + ", ".join(mine + ([terms] if terms else []))
    return text[:MAX_CHARS].rsplit(",", 1)[0] if len(text) > MAX_CHARS else text


def apply_dictionary(home: Path, text: str) -> str:
    """His `heard => wanted` lines applied as whole-word, any-case replacements, like dictation does."""
    import re
    try:
        lines = (home / "dictionary.txt").read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):             # unreadable, or not UTF-8: no extras, never a crash
        return text
    for line in lines:
        parts = [p.strip() for p in line.split("=>")]
        if len(parts) == 2 and parts[0] and not parts[0].startswith("#"):
            text = re.sub(r"\b" + re.escape(parts[0]) + r"\b", lambda _m, w=parts[1]: w, text, flags=re.I)
    return text
