"""Body-style image mapping for interior kit pages.

Maps each year/make/model to an exterior image (vehicle-type aware, with
per-popular-model overrides) and a bright interior reference image.
"""

import re

TRUCK_PREFIX = (
    "tacoma", "tundra", "f150", "f250", "f350", "f450", "silverado", "sierra",
    "ram1500", "ram2500", "ram3500", "frontier", "ridgeline", "ranger",
    "colorado", "canyon", "titan", "gladiator", "maverick", "santacruz",
    "dakota", "avalanche", "raptor", "cybertruck",
)
TRUCK_EXACT = {"1500", "2500", "3500"}  # Ram model names

SUV_PREFIX = (
    "4runner", "rav4", "crv", "pilot", "passport", "highlander", "wrangler",
    "cherokee", "wagoneer", "tahoe", "suburban", "yukon", "escalade",
    "explorer", "expedition", "bronco", "traverse", "equinox", "trailblazer",
    "blazer", "trax", "pathfinder", "armada", "xterra", "rogue", "murano",
    "kicks", "sequoia", "landcruiser", "forester", "outback", "crosstrek",
    "ascent", "sorento", "sportage", "telluride", "palisade", "santafe",
    "tucson", "kona", "venue", "cx5", "cx50", "cx9", "cx90", "cx30",
    "mdx", "rdx", "xt4", "xt5", "xt6", "enclave", "encore", "acadia",
    "terrain", "edge", "escape", "corsair", "nautilus", "aviator",
    "navigator", "defender", "discovery", "rangerover", "rangesport",
    "velar", "evoque", "compass", "renegade", "q5", "q7", "q8", "q3",
    "x3", "x5", "x7", "x1", "rx", "nx", "gx", "lx", "ux", "glc", "gle",
    "gls", "gla", "glb", "macan", "cayenne", "modely", "modelx", "lyriq",
    "ev9", "ev6", "ioniq5",
    # minivans / vans ride on the SUV-style images
    "odyssey", "sienna", "pacifica", "caravan", "sedona", "carnival",
    "transit", "metris", "sprinter", "promaster", "savana", "express",
    "econoline", "quest", "villager", "windstar", "freestar", "uplander",
    "nv200", "nv1500", "nv2500", "nv3500",
)

SPORT_PREFIX = (
    "mustang", "camaro", "challenger", "charger", "corvette", "370z",
    "350z", "gtr", "supra", "miata", "mx5", "s2000", "rx8", "rx7", "m2",
    "m4", "z4", "nsx", "q60", "g37", "g35", "veloster", "viper", "fiero",
    "solstice", "crossfire",
)
SPORT_EXACT = {"86", "gr86", "brz", "tt", "911", "boxster", "cayman",
               "wrx", "sti", "rc", "lc"}

# Popular models get their own recognizable exterior shot.
# Key: (make_norm, model_norm) -> image stem.
POPULAR_MODELS = {
    ("toyota", "tacoma"): "model-tacoma",
    ("ford", "f150"): "model-f150",
    ("chevrolet", "silverado"): "model-silverado",
    ("chevrolet", "silverado1500"): "model-silverado",
    ("ram", "1500"): "model-ram",
    ("jeep", "wrangler"): "model-wrangler",
    ("toyota", "4runner"): "model-4runner",
    ("honda", "civic"): "model-civic",
    ("toyota", "corolla"): "model-corolla",
    ("toyota", "camry"): "model-camry",
    ("honda", "accord"): "model-accord",
    ("honda", "crv"): "model-cr-v",
    ("toyota", "rav4"): "model-rav4",
}

BODY_EXT = {"truck": "ext-truck", "suv": "ext-suv", "car": "ext-sedan",
            "sport": "ext-sport"}
BODY_INT = {"truck": "int-truck", "suv": "int-suv", "car": "int-car",
            "sport": "int-car"}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def body_style(make, model):
    m = _norm(model)
    if m in TRUCK_EXACT or m.startswith(TRUCK_PREFIX):
        return "truck"
    if m in SPORT_EXACT or m.startswith(SPORT_PREFIX):
        return "sport"
    if m.startswith(SUV_PREFIX):
        return "suv"
    return "car"


def kit_images(make, model):
    """Return (exterior_src, interior_src) static paths for a vehicle."""
    bs = body_style(make, model)
    stem = POPULAR_MODELS.get((_norm(make), _norm(model)), BODY_EXT[bs])
    return (f"/static/img/kits/{stem}.jpg",
            f"/static/img/kits/{BODY_INT[bs]}.jpg")
