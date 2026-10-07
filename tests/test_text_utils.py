from job_agent.text_utils import extract_experience, looks_remote, normalise_city


def test_city_aliases():
    assert normalise_city("Bangalore") == "Bengaluru"
    assert normalise_city("Gurugram") == "Gurgaon"
    assert normalise_city("Kochi") == "Kochi"
    assert normalise_city(None) is None


def test_experience_ranges():
    assert extract_experience("Need 2-4 years of Python") == (2, 4)
    assert extract_experience("0 to 1 yrs experience") == (0, 1)
    assert extract_experience("3+ years in ML") == (3, None)
    assert extract_experience("Freshers welcome") == (0, 1)
    assert extract_experience("Great team, free lunch") == (None, None)


def test_remote():
    assert looks_remote("ML Engineer (Remote)")
    assert looks_remote("", "Work from home available")
    assert not looks_remote("ML Engineer", "Office in Pune")


def test_neighbourhoods_roll_up_to_city():
    from job_agent.text_utils import city_from_area

    assert city_from_area(["India", "Karnataka", "Bangalore", "Whitefield"]) == "Bengaluru"
    assert city_from_area(["India", "Karnataka", "Bangalore Urban", "Koramangala"]) == "Bengaluru"
    assert city_from_area(["India", "Maharashtra", "Pune", "Bhosarigoan"]) == "Pune"
    assert city_from_area(["India", "Karnataka", "Mysore", "Vijayanagar"]) == "Mysore"
    assert city_from_area(["India", "Haryana"]) == "Haryana"
    assert city_from_area(["India"]) is None
