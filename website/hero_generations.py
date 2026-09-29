"""Popular-model generation map for hero images on fit pages.

Keyed by (make, model) -> list of (start_year, end_year, label) generations.
Covers high-volume US models 1990+ still commonly on the road.
European makes skipped by user decision (token cost).
end_year of None = current generation (use 2026).
"""

HERO_GENERATIONS = {
    # ---------------- Toyota ----------------
    ("Toyota", "Corolla"): [(1993, 1997, "7th gen"), (1998, 2002, "8th gen"),
                            (2003, 2008, "9th gen"), (2009, 2013, "10th gen"),
                            (2014, 2019, "11th gen"), (2020, None, "12th gen")],
    ("Toyota", "Camry"): [(1992, 1996, "4th gen"), (1997, 2001, "5th gen"),
                          (2002, 2006, "6th gen"), (2007, 2011, "7th gen"),
                          (2012, 2017, "8th gen"), (2018, 2024, "9th gen"),
                          (2025, None, "10th gen")],
    ("Toyota", "Tacoma"): [(1995, 2004, "1st gen"), (2005, 2015, "2nd gen"),
                           (2016, 2023, "3rd gen"), (2024, None, "4th gen")],
    ("Toyota", "4Runner"): [(1996, 2002, "3rd gen"), (2003, 2009, "4th gen"),
                            (2010, 2024, "5th gen"), (2025, None, "6th gen")],
    ("Toyota", "RAV4"): [(1996, 2000, "1st gen"), (2001, 2005, "2nd gen"),
                         (2006, 2012, "3rd gen"), (2013, 2018, "4th gen"),
                         (2019, None, "5th gen")],
    ("Toyota", "Highlander"): [(2001, 2007, "1st gen"), (2008, 2013, "2nd gen"),
                               (2014, 2019, "3rd gen"), (2020, None, "4th gen")],
    ("Toyota", "Tundra"): [(2000, 2006, "1st gen"), (2007, 2013, "2nd gen"),
                           (2014, 2021, "2nd gen facelift"), (2022, None, "3rd gen")],
    ("Toyota", "Prius"): [(2001, 2003, "1st gen"), (2004, 2009, "2nd gen"),
                          (2010, 2015, "3rd gen"), (2016, 2022, "4th gen"),
                          (2023, None, "5th gen")],
    ("Toyota", "Sienna"): [(1998, 2003, "1st gen"), (2004, 2010, "2nd gen"),
                           (2011, 2020, "3rd gen"), (2021, None, "4th gen")],
    ("Toyota", "Sequoia"): [(2001, 2007, "1st gen"), (2008, 2022, "2nd gen"),
                            (2023, None, "3rd gen")],
    # ---------------- Nissan ----------------
    ("Nissan", "Altima"): [(1993, 1997, "1st gen"), (1998, 2001, "2nd gen"),
                           (2002, 2006, "3rd gen"), (2007, 2012, "4th gen"),
                           (2013, 2018, "5th gen"), (2019, None, "6th gen")],
    ("Nissan", "Sentra"): [(1995, 1999, "4th gen"), (2000, 2006, "5th gen"),
                           (2007, 2012, "6th gen"), (2013, 2019, "7th gen"),
                           (2020, None, "8th gen")],
    ("Nissan", "Frontier"): [(1998, 2004, "1st gen"), (2005, 2021, "2nd gen"),
                             (2022, None, "3rd gen")],
    ("Nissan", "Pathfinder"): [(1996, 2004, "2nd gen"), (2005, 2012, "3rd gen"),
                               (2013, 2020, "4th gen"), (2021, None, "5th gen")],
    ("Nissan", "Xterra"): [(2000, 2004, "1st gen"), (2005, 2015, "2nd gen")],
    ("Nissan", "Titan"): [(2004, 2015, "1st gen"), (2016, 2024, "2nd gen")],
    ("Nissan", "Rogue"): [(2008, 2013, "1st gen"), (2014, 2020, "2nd gen"),
                          (2021, None, "3rd gen")],
    ("Nissan", "Murano"): [(2003, 2007, "1st gen"), (2009, 2014, "2nd gen"),
                           (2015, 2024, "3rd gen"), (2025, None, "4th gen")],
    ("Nissan", "Maxima"): [(1995, 1999, "4th gen"), (2000, 2003, "5th gen"),
                           (2004, 2008, "6th gen"), (2009, 2014, "7th gen"),
                           (2016, 2023, "8th gen")],
    # ---------------- Honda ----------------
    ("Honda", "Civic"): [(1992, 1995, "5th gen"), (1996, 2000, "6th gen"),
                         (2001, 2005, "7th gen"), (2006, 2011, "8th gen"),
                         (2012, 2015, "9th gen"), (2016, 2021, "10th gen"),
                         (2022, None, "11th gen")],
    ("Honda", "Accord"): [(1994, 1997, "5th gen"), (1998, 2002, "6th gen"),
                          (2003, 2007, "7th gen"), (2008, 2012, "8th gen"),
                          (2013, 2017, "9th gen"), (2018, 2022, "10th gen"),
                          (2023, None, "11th gen")],
    ("Honda", "CR-V"): [(1997, 2001, "1st gen"), (2002, 2006, "2nd gen"),
                        (2007, 2011, "3rd gen"), (2012, 2016, "4th gen"),
                        (2017, 2022, "5th gen"), (2023, None, "6th gen")],
    ("Honda", "Pilot"): [(2003, 2008, "1st gen"), (2009, 2015, "2nd gen"),
                         (2016, 2022, "3rd gen"), (2023, None, "4th gen")],
    ("Honda", "Odyssey"): [(1999, 2004, "2nd gen"), (2005, 2010, "3rd gen"),
                           (2011, 2017, "4th gen"), (2018, None, "5th gen")],
    ("Honda", "Ridgeline"): [(2006, 2014, "1st gen"), (2017, None, "2nd gen")],
    # ---------------- Ford ----------------
    ("Ford", "F-150"): [(1987, 1991, "8th gen"), (1992, 1996, "9th gen"),
                        (1997, 2003, "10th gen"), (2004, 2008, "11th gen"),
                        (2009, 2014, "12th gen"), (2015, 2020, "13th gen"),
                        (2021, None, "14th gen")],
    ("Ford", "F-250"): [(1987, 1991, "8th gen"), (1992, 1997, "9th gen")],
    ("Ford", "F-350"): [(1987, 1991, "8th gen"), (1992, 1997, "9th gen")],
    ("Ford", "Mustang"): [(1994, 2004, "SN-95"), (2005, 2014, "S197"),
                          (2015, 2023, "S550"), (2024, None, "S650")],
    ("Ford", "Explorer"): [(1995, 2001, "2nd gen"), (2002, 2005, "3rd gen"),
                           (2006, 2010, "4th gen"), (2011, 2019, "5th gen"),
                           (2020, None, "6th gen")],
    ("Ford", "Escape"): [(2001, 2007, "1st gen"), (2008, 2012, "2nd gen"),
                         (2013, 2019, "3rd gen"), (2020, None, "4th gen")],
    ("Ford", "Ranger"): [(1993, 1997, "3rd gen"), (1998, 2011, "4th gen"),
                         (2019, 2023, "5th gen US"), (2024, None, "6th gen US")],
    ("Ford", "Expedition"): [(1997, 2002, "1st gen"), (2003, 2006, "2nd gen"),
                             (2007, 2017, "3rd gen"), (2018, None, "4th gen")],
    ("Ford", "Edge"): [(2007, 2014, "1st gen"), (2015, 2024, "2nd gen")],
    ("Ford", "Taurus"): [(1996, 1999, "3rd gen"), (2000, 2007, "4th gen"),
                         (2008, 2009, "5th gen"), (2010, 2019, "6th gen")],
    # ---------------- Chevrolet ----------------
    ("Chevrolet", "Silverado"): [(1999, 2006, "1st gen"), (2007, 2013, "2nd gen"),
                                 (2014, 2019, "3rd gen"), (2020, None, "4th gen")],
    # GMT400 C/K trucks (1988-1998): same body, one banner reused per make.
    ("Chevrolet", "C1500"): [(1988, 1999, "GMT400")],
    ("Chevrolet", "K1500"): [(1988, 1999, "GMT400")],
    ("Chevrolet", "C2500"): [(1988, 1999, "GMT400")],
    ("Chevrolet", "K2500"): [(1988, 1999, "GMT400")],
    ("Chevrolet", "C3500"): [(1988, 1999, "GMT400")],
    ("Chevrolet", "K3500"): [(1988, 1999, "GMT400")],
    ("Chevrolet", "Equinox"): [(2005, 2009, "1st gen"), (2010, 2017, "2nd gen"),
                               (2018, 2024, "3rd gen"), (2025, None, "4th gen")],
    ("Chevrolet", "Tahoe"): [(1995, 1999, "1st gen"), (2000, 2006, "2nd gen"),
                             (2007, 2014, "3rd gen"), (2015, 2020, "4th gen"),
                             (2021, None, "5th gen")],
    ("Chevrolet", "Suburban"): [(1992, 1999, "8th gen"), (2000, 2006, "9th gen"),
                                (2007, 2014, "10th gen"), (2015, 2020, "11th gen"),
                                (2021, None, "12th gen")],
    ("Chevrolet", "Malibu"): [(1997, 2003, "5th gen"), (2004, 2007, "6th gen"),
                              (2008, 2012, "7th gen"), (2013, 2015, "8th gen"),
                              (2016, 2025, "9th gen")],
    ("Chevrolet", "Colorado"): [(2004, 2012, "1st gen"), (2015, 2022, "2nd gen"),
                                (2023, None, "3rd gen")],
    ("Chevrolet", "Traverse"): [(2009, 2017, "1st gen"), (2018, 2023, "2nd gen"),
                                (2024, None, "3rd gen")],
    ("Chevrolet", "Camaro"): [(1993, 2002, "4th gen"), (2010, 2015, "5th gen"),
                              (2016, 2024, "6th gen")],
    # ---------------- GMC ----------------
    ("GMC", "Sierra"): [(1999, 2006, "1st gen"), (2007, 2013, "2nd gen"),
                        (2014, 2019, "3rd gen"), (2020, None, "4th gen")],
    # GMT400 C/K trucks (1988-1998): same body, one banner reused per make.
    ("GMC", "C1500"): [(1988, 1999, "GMT400")],
    ("GMC", "K1500"): [(1988, 1999, "GMT400")],
    ("GMC", "C2500"): [(1988, 1999, "GMT400")],
    ("GMC", "K2500"): [(1988, 1999, "GMT400")],
    ("GMC", "C3500"): [(1988, 1999, "GMT400")],
    ("GMC", "K3500"): [(1988, 1999, "GMT400")],
    ("GMC", "Yukon"): [(1992, 1999, "1st gen"), (2000, 2006, "2nd gen"),
                       (2007, 2014, "3rd gen"), (2015, 2020, "4th gen"),
                       (2021, None, "5th gen")],
    ("GMC", "Acadia"): [(2007, 2016, "1st gen"), (2017, 2023, "2nd gen"),
                        (2024, None, "3rd gen")],
    ("GMC", "Canyon"): [(2004, 2012, "1st gen"), (2015, 2022, "2nd gen"),
                        (2023, None, "3rd gen")],
    # ---------------- Dodge / Ram ----------------
    ("Dodge", "Charger"): [(2006, 2010, "6th gen"), (2011, 2023, "7th gen")],
    ("Dodge", "Durango"): [(1998, 2003, "1st gen"), (2004, 2009, "2nd gen"),
                           (2011, None, "3rd gen")],
    ("Dodge", "Grand Caravan"): [(1996, 2000, "3rd gen"), (2001, 2007, "4th gen"),
                                 (2008, 2020, "5th gen")],
    ("Dodge", "Journey"): [(2009, 2020, "1st gen")],
    # 1990-1993 D/W series (1st-gen Ram body) and 1994-2010 Rams sold as Dodge.
    ("Dodge", "D150"): [(1990, 1993, "1st gen")],
    ("Dodge", "W150"): [(1990, 1993, "1st gen")],
    ("Dodge", "D250"): [(1990, 1993, "1st gen")],
    ("Dodge", "W250"): [(1990, 1993, "1st gen")],
    ("Dodge", "D350"): [(1990, 1993, "1st gen")],
    ("Dodge", "Ram 1500"): [(1994, 2001, "2nd gen"), (2002, 2008, "3rd gen"),
                            (2009, 2010, "4th gen")],
    ("Dodge", "Ram 2500"): [(1994, 2002, "2nd gen"), (2003, 2009, "3rd gen"),
                            (2010, 2010, "4th gen")],
    ("Ram", "1500"): [(1994, 2001, "2nd gen"), (2002, 2008, "3rd gen"),
                      (2009, 2018, "4th gen"), (2019, None, "5th gen")],
    ("Ram", "2500"): [(1994, 2002, "2nd gen"), (2003, 2009, "3rd gen"),
                      (2010, 2018, "4th gen"), (2019, None, "5th gen")],
    # ---------------- Subaru ----------------
    ("Subaru", "Outback"): [(1995, 1999, "1st gen"), (2000, 2004, "2nd gen"),
                            (2005, 2009, "3rd gen"), (2010, 2014, "4th gen"),
                            (2015, 2019, "5th gen"), (2020, 2025, "6th gen"),
                            (2026, None, "7th gen")],
    ("Subaru", "Forester"): [(1998, 2002, "1st gen"), (2003, 2008, "2nd gen"),
                             (2009, 2013, "3rd gen"), (2014, 2018, "4th gen"),
                             (2019, 2024, "5th gen"), (2025, None, "6th gen")],
    ("Subaru", "Impreza"): [(1993, 2001, "1st gen"), (2002, 2007, "2nd gen"),
                            (2008, 2011, "3rd gen"), (2012, 2016, "4th gen"),
                            (2017, 2023, "5th gen"), (2024, None, "6th gen")],
    ("Subaru", "Legacy"): [(1995, 1999, "2nd gen"), (2000, 2004, "3rd gen"),
                           (2005, 2009, "4th gen"), (2010, 2014, "5th gen"),
                           (2015, 2019, "6th gen"), (2020, 2025, "7th gen")],
    ("Subaru", "Crosstrek"): [(2013, 2017, "1st gen"), (2018, 2023, "2nd gen"),
                              (2024, None, "3rd gen")],
    # ---------------- Scion ----------------
    ("Scion", "tC"): [(2005, 2010, "1st gen"), (2011, 2016, "2nd gen")],
    ("Scion", "xB"): [(2004, 2006, "1st gen"), (2008, 2015, "2nd gen")],
    ("Scion", "FR-S"): [(2013, 2016, "1st gen")],
}

CURRENT_YEAR = 2026


def hero_generation(make, model, year):
    """Return the generation label for a vehicle, or None if not mapped."""
    gens = HERO_GENERATIONS.get((make, model))
    if not gens:
        return None
    for start, end, label in gens:
        end = end or CURRENT_YEAR
        if start <= int(year) <= end:
            return label
    return None


# Trailing model-name tokens that are trim/series qualifiers, not part of the
# base model name. Lets "Silverado 1500 HD" fall back to the "Silverado"
# generation map while "Civic del Sol" (not a qualifier) stays unmatched
# instead of showing the wrong car's banner.
_TRIM_SUFFIX_TOKENS = frozenset({
    "100", "150", "200", "250", "300", "350", "1500", "2500", "3500",
    "4500", "5500", "6500", "7500",
    "hd", "classic", "heritage", "limited", "ld", "custom", "lt", "ls",
    "wt", "xl", "xlt",
})


def _base_model(model):
    """Strip trailing trim/series qualifiers: 'Silverado 1500 HD Classic'
    -> 'Silverado'. Returns the model unchanged if nothing strips."""
    words = model.split()
    while len(words) > 1 and words[-1].lower() in _TRIM_SUFFIX_TOKENS:
        words.pop()
    return " ".join(words)


def hero_slug(make, model, year):
    """File stem for the generation hero image, or None.

    One image per generation: the slug uses the generation's start year,
    so every year in the generation shares the same image.
    """
    gens = HERO_GENERATIONS.get((make, model))
    slug_model = model
    if not gens:
        base = _base_model(model)
        if base != model:
            gens = HERO_GENERATIONS.get((make, base))
            slug_model = base
    if not gens:
        return None
    for start, end, _ in gens:
        if start <= int(year) <= (end or CURRENT_YEAR):
            mk = make.lower().replace(" ", "-")
            mo = slug_model.lower().replace(" ", "-")
            return f"hero-{mk}-{mo}-{start}"
    return None


# --- Batch 2: remaining popular passenger models from the same 10 makes ---
HERO_GENERATIONS_EXTRA = {
    ("Toyota", "Avalon"): [(1995, 1999, "1st gen"), (2000, 2004, "2nd gen"), (2005, 2012, "3rd gen"), (2013, 2018, "4th gen"), (2019, 2022, "5th gen")],
    ("Toyota", "Land Cruiser"): [(1990, 1997, "80 series"), (1998, 2007, "100 series"), (2008, 2021, "200 series"), (2024, None, "250 series")],
    ("Toyota", "Celica"): [(1990, 1993, "5th gen"), (1994, 1999, "6th gen"), (2000, 2005, "7th gen")],
    ("Toyota", "Yaris"): [(2007, 2011, "2nd gen"), (2012, 2019, "3rd gen"), (2020, None, "4th gen")],
    ("Toyota", "Matrix"): [(2003, 2008, "1st gen"), (2009, 2013, "2nd gen")],
    ("Toyota", "Solara"): [(1999, 2003, "1st gen"), (2004, 2008, "2nd gen")],
    ("Toyota", "Tercel"): [(1991, 1994, "4th gen"), (1995, 1999, "5th gen")],
    ("Toyota", "FJ Cruiser"): [(2007, 2014, "1st gen")],
    ("Toyota", "Venza"): [(2009, 2015, "1st gen"), (2021, 2024, "2nd gen")],
    ("Toyota", "Supra"): [(1993, 1998, "Mk4"), (2020, None, "Mk5")],
    ("Toyota", "Prius C"): [(2012, 2019, "1st gen")],
    ("Toyota", "Prius V"): [(2012, 2017, "1st gen")],
    ("Toyota", "Echo"): [(2000, 2005, "1st gen")],
    ("Toyota", "Previa"): [(1991, 1997, "1st gen")],
    ("Toyota", "MR2"): [(1991, 1995, "SW20"), (2000, 2005, "Spyder")],
    ("Toyota", "T100"): [(1993, 1998, "1st gen")],
    ("Nissan", "Quest"): [(1993, 1998, "1st gen"), (1999, 2002, "2nd gen"), (2004, 2009, "3rd gen"), (2011, 2017, "4th gen")],
    ("Nissan", "Armada"): [(2004, 2015, "1st gen"), (2017, None, "2nd gen")],
    ("Nissan", "Versa"): [(2007, 2011, "1st gen"), (2012, 2019, "2nd gen"), (2020, None, "3rd gen")],
    ("Nissan", "370Z"): [(2009, 2020, "1st gen")],
    ("Nissan", "GT-R"): [(2009, None, "R35")],
    ("Nissan", "Leaf"): [(2011, 2017, "1st gen"), (2018, None, "2nd gen")],
    ("Nissan", "Juke"): [(2011, 2017, "1st gen")],
    ("Nissan", "Cube"): [(2009, 2014, "3rd gen")],
    ("Nissan", "350Z"): [(2003, 2008, "Z33")],
    ("Nissan", "240SX"): [(1990, 1994, "S13"), (1995, 1998, "S14")],
    ("Nissan", "300ZX"): [(1990, 1996, "Z32")],
    ("Honda", "Fit"): [(2007, 2008, "1st gen"), (2009, 2013, "2nd gen"), (2015, 2020, "3rd gen")],
    ("Honda", "Prelude"): [(1992, 1996, "4th gen"), (1997, 2001, "5th gen")],
    ("Honda", "Insight"): [(2000, 2006, "1st gen"), (2010, 2014, "2nd gen"), (2019, 2022, "3rd gen")],
    ("Honda", "S2000"): [(2000, 2009, "AP1/AP2")],
    ("Honda", "Element"): [(2003, 2011, "1st gen")],
    ("Honda", "Passport"): [(1994, 2002, "1st-2nd gen"), (2019, None, "3rd gen")],
    ("Honda", "CR-Z"): [(2011, 2016, "1st gen")],
    ("Ford", "Focus"): [(2000, 2007, "1st gen"), (2008, 2011, "2nd gen"), (2012, 2018, "3rd gen")],
    ("Ford", "Fusion"): [(2006, 2012, "1st gen"), (2013, 2020, "2nd gen")],
    ("Ford", "Crown Victoria"): [(1992, 1997, "1st gen"), (1998, 2011, "2nd gen")],
    ("Ford", "Flex"): [(2009, 2019, "1st gen")],
    ("Ford", "Escort"): [(1991, 1996, "2nd gen"), (1997, 2003, "3rd gen")],
    ("Ford", "Thunderbird"): [(1990, 1997, "10th gen"), (2002, 2005, "11th gen")],
    ("Ford", "Bronco"): [(1992, 1996, "5th gen"), (2021, None, "6th gen")],
    ("Ford", "Fiesta"): [(2011, 2019, "6th gen")],
    ("Ford", "Explorer Sport Trac"): [(2001, 2005, "1st gen"), (2007, 2010, "2nd gen")],
    ("Ford", "Transit Connect"): [(2010, 2013, "1st gen"), (2014, 2023, "2nd gen")],
    ("Ford", "C-Max"): [(2013, 2018, "1st gen")],
    ("Chevrolet", "Corvette"): [(1990, 1996, "C4"), (1997, 2004, "C5"), (2005, 2013, "C6"), (2014, 2019, "C7"), (2020, None, "C8")],
    ("Chevrolet", "Impala"): [(1994, 1996, "SS"), (2000, 2005, "8th gen"), (2006, 2013, "9th gen"), (2014, 2020, "10th gen")],
    ("Chevrolet", "Blazer"): [(1995, 2005, "2nd gen"), (2019, None, "3rd gen")],
    ("Chevrolet", "Cavalier"): [(1995, 2005, "3rd gen")],
    ("Chevrolet", "S10"): [(1994, 2004, "2nd gen")],
    ("Chevrolet", "Monte Carlo"): [(1995, 1999, "5th gen"), (2000, 2007, "6th gen")],
    ("Chevrolet", "Lumina"): [(1990, 1994, "1st gen"), (1995, 2001, "2nd gen")],
    ("Chevrolet", "Caprice"): [(1991, 1996, "4th gen")],
    ("Chevrolet", "Cobalt"): [(2005, 2010, "1st gen")],
    ("Chevrolet", "Cruze"): [(2011, 2016, "1st gen"), (2016, 2019, "2nd gen")],
    ("Chevrolet", "Sonic"): [(2012, 2020, "1st gen")],
    ("Chevrolet", "Spark"): [(2013, 2022, "3rd-4th gen")],
    ("Chevrolet", "Aveo"): [(2004, 2011, "1st gen")],
    ("Chevrolet", "HHR"): [(2006, 2011, "1st gen")],
    ("Chevrolet", "Tracker"): [(1999, 2004, "2nd gen")],
    ("Chevrolet", "Trailblazer"): [(2002, 2009, "1st gen"), (2021, None, "2nd gen")],
    ("Chevrolet", "Trax"): [(2015, None, "1st gen")],
    ("Chevrolet", "Volt"): [(2011, 2015, "1st gen"), (2016, 2019, "2nd gen")],
    ("Chevrolet", "Avalanche"): [(2002, 2006, "1st gen"), (2007, 2013, "2nd gen")],
    ("Chevrolet", "Beretta"): [(1990, 1996, "1st gen")],
    ("Chevrolet", "Corsica"): [(1990, 1996, "1st gen")],
    ("GMC", "Jimmy"): [(1995, 2005, "2nd gen")],
    ("GMC", "Sonoma"): [(1994, 2004, "2nd gen")],
    ("GMC", "Envoy"): [(1998, 2009, "1st gen")],
    ("GMC", "Terrain"): [(2010, 2017, "1st gen"), (2018, None, "2nd gen")],
    ("Dodge", "Challenger"): [(2008, 2014, "3rd gen"), (2015, 2023, "facelift")],
    ("Dodge", "Dakota"): [(1997, 2004, "3rd gen"), (2005, 2011, "4th gen")],
    ("Dodge", "Caravan"): [(1996, 2000, "3rd gen"), (2001, 2007, "4th gen")],
    ("Dodge", "Neon"): [(1995, 1999, "1st gen"), (2000, 2005, "2nd gen")],
    ("Dodge", "Avenger"): [(1995, 2000, "1st gen"), (2008, 2014, "2nd gen")],
    ("Dodge", "Stratus"): [(1995, 2000, "1st gen"), (2001, 2006, "2nd gen")],
    ("Dodge", "Intrepid"): [(1993, 1997, "1st gen"), (1998, 2004, "2nd gen")],
    ("Dodge", "Viper"): [(1992, 2002, "SR"), (2003, 2010, "ZB"), (2013, 2017, "VX")],
    ("Dodge", "Caliber"): [(2007, 2012, "1st gen")],
    ("Dodge", "Nitro"): [(2007, 2012, "1st gen")],
    ("Subaru", "BRZ"): [(2013, 2021, "1st gen"), (2022, None, "2nd gen")],
    ("Subaru", "WRX"): [(2002, 2007, "GD"), (2008, 2014, "GE/GH"), (2015, 2021, "VA"), (2022, None, "VB")],
    ("Subaru", "Tribeca"): [(2006, 2014, "1st gen")],
    ("Scion", "xD"): [(2008, 2014, "1st gen")],
    ("Scion", "xA"): [(2004, 2006, "1st gen")],
}

HERO_GENERATIONS.update(HERO_GENERATIONS_EXTRA)
