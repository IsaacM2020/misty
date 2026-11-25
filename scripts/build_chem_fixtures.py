"""Write the chemistry fixture slates (tests/engine/fixtures/slates/good/*-lewis.json): a molecule, one facts line from code, a shape answer."""
import json, pathlib, re
root = pathlib.Path(__file__).resolve().parent.parent / "tests/engine/fixtures/slates/good"
MOL = [("water", "H2O", 0, "Water"), ("co2", "CO2", 0, "Carbon dioxide"), ("methane", "CH4", 0, "Methane"), ("ammonia", "NH3", 0, "Ammonia"),
       ("oxygen", "O2", 0, "Oxygen"), ("nitrogen", "N2", 0, "Nitrogen"), ("hcn", "HCN", 0, "Hydrogen cyanide"),
       ("methanal", "CH2O", 0, "Methanal"), ("ammonium", "NH4", "+", "Ammonium"), ("hydroxide", "OH", "-", "Hydroxide"),
       ("carbonate", "CO3", "2-", "Carbonate"), ("so2", "SO2", 0, "Sulfur dioxide"), ("bf3", "BF3", 0, "Boron trifluoride"),
       ("pcl5", "PCl5", 0, "Phosphorus pentachloride"), ("ethene", "C2H4", 0, "Ethene"), ("ozone", "O3", 0, "Ozone"),
       ("xef4", "XeF4", 0, "Xenon tetrafluoride"), ("sf6", "SF6", 0, "Sulfur hexafluoride")]
for name, f, q, title in MOL:
    tex = re.sub(r"(\d+)", r"_{\1}", f)
    sup = "" if not q else "^{" + q.replace("-", "-") + "}"
    blocks = [
        {"k": "diagram", "items": [{"t": "molecule", "formula": f, **({"charge": q} if q else {})}], "say": f"Here is the Lewis structure of {title.lower()}."},
        {"k": "text", "text": "\\ve valence electrons, \\bonds bonds, \\lps lone pairs.", "say": "\\ve valence electrons, \\bonds bonds, \\lps lone pairs."},
        {"k": "math", "tex": "\\mathrm{" + tex + "}" + sup + ":\\ \\ans", "ans": {"chem": "geom"}, "say": "So the shape is \\geom."}]
    (root / f"lewis-{name}.json").write_text(json.dumps({"_kind": "quick", "title": title, "blocks": blocks}, indent=1) + "\n")
print(len(MOL), "chemistry fixtures")
