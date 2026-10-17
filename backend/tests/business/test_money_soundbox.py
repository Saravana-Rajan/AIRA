"""Task 2: rupee change maths, ta/hi/en number words, and the soundbox payment parser.

Review focus: a soundbox transcript that says sent / debited / failed / not received, names two different
amounts, or carries paise must give None, so the tool asks instead of guessing. Number words must never
produce a wrong amount: a phrase that cannot be read exactly is dropped (or poisons the soundbox read).
"""
from fractions import Fraction

import pytest

from aira_live.business import money, numwords, soundbox

# --- money ---------------------------------------------------------------------------------------------


def test_change_due_basic():
    assert money.change_due(120, 200) == 80
    assert money.change_due(120, 120) == 0


def test_change_due_insufficient():
    with pytest.raises(money.InsufficientTender):
        money.change_due(120, 100)


def test_notes_for_80():
    assert money.notes_for(80) == [{"denomination": 50, "count": 1}, {"denomination": 20, "count": 1},
                                   {"denomination": 10, "count": 1}]


def test_notes_for_zero_and_large():
    assert money.notes_for(0) == []
    assert money.notes_for(1400) == [{"denomination": 500, "count": 2}, {"denomination": 200, "count": 2}]


def test_total_of():
    assert money.total_of([{"denomination": 500, "count": 2}, {"denomination": 100, "count": 4}]) == 1400


def test_insufficient_tender_says_how_short():
    with pytest.raises(money.InsufficientTender) as exc:
        money.change_due(120, 100)
    assert exc.value.short == 20


def test_money_rejects_paise_and_negatives():
    for bad in [(120.5, 200), (120, 200.25), (-1, 10), (10, -1)]:
        with pytest.raises(ValueError):
            money.change_due(*bad)
    with pytest.raises(ValueError):
        money.notes_for(-5)
    with pytest.raises(ValueError):
        money.notes_for(10.5)
    with pytest.raises(TypeError):
        money.change_due(True, 10)
    assert money.change_due(120.0, 200.0) == 80        # integral floats (JSON) are fine


def test_notes_for_never_plans_a_2000_note():
    assert 2000 not in money.DENOMINATIONS
    plan = money.notes_for(4000)
    assert plan == [{"denomination": 500, "count": 8}]
    assert money.total_of(plan) == 4000
    for amount in (1, 3, 7, 88, 499, 1999, 2763):
        assert money.total_of(money.notes_for(amount)) == amount


def test_total_of_counts_a_2000_note_and_rejects_bad_counts():
    assert money.total_of([{"denomination": 2000, "count": 1}, {"denomination": 10, "count": 3}]) == 2030
    assert money.total_of([]) == 0
    with pytest.raises(ValueError):
        money.total_of([{"denomination": 100, "count": -1}])
    with pytest.raises(ValueError):
        money.total_of([{"denomination": 0, "count": 1}])


# --- number words --------------------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("received rupees sixty", [60]),
    ("one thousand two hundred fifty", [1250]),
    ("two lakh", [200000]),
    ("साठ रुपये", [60]),
    ("एक सौ बीस", [120]),
    ("दो हज़ार पांच सौ", [2500]),
    ("அறுபது ரூபாய்", [60]),
    ("இருபத்தி ஐந்து", [25]),
    ("நூற்றி இருபது", [120]),
    ("இரண்டாயிரத்து ஐநூறு", [2500]),
])
def test_amounts_in_words(text, expected):
    assert numwords.amounts_in_words(text) == expected


@pytest.mark.parametrize("text,expected", [
    # English grammar: separate numbers stay separate, never summed
    ("sixty and eighty", [60, 80]),
    ("one hundred and twenty five", [125]),
    ("a hundred and twenty", [120]),
    ("twenty five hundred", [2500]),
    ("twenty-five", [25]),
    ("one lakh twenty thousand", [120000]),
    ("five six", [5, 6]),
    # Hindi: nukta / chandrabindu spellings and the fractional words
    ("दो हजार पाँच सौ", [2500]),
    ("डेढ़ सौ", [150]),
    ("डेढ सौ", [150]),
    ("ढाई सौ", [250]),
    ("साढ़े तीन सौ", [350]),
    ("सवा सौ", [125]),
    ("पौने दो सौ", [175]),
    ("ढाई हज़ार", [2500]),
    # Tamil: fused (sandhi) forms, multipliers, and ஒரு as an article
    ("இருபத்தைந்து", [25]),
    ("நூற்றிருபது", [120]),
    ("நூற்றியிருபத்தைந்து", [125]),
    ("நூற்றுப்பத்து", [110]),
    ("ஆயிரத்து இருநூறு", [1200]),
    ("ஆயிரம்", [1000]),
    ("மூன்று ஆயிரம்", [3000]),
    ("ஒரு லட்சம்", [100000]),
    ("இருபதாயிரம்", [20000]),
    ("ஒன்றரை லட்சம்", [150000]),
    ("ஒரு லட்சத்து இருபத்தி ஐந்து ஆயிரம்", [125000]),
    ("ஒரு பாக்கெட் பால்", []),
])
def test_amounts_in_words_grammar(text, expected):
    assert numwords.amounts_in_words(text) == expected


@pytest.mark.parametrize("text", [
    "நூற்றி",                    # combining form with nothing after it
    "நூற்றி இருபத்தைஞ்சு",        # combining form followed by an unknown fused number
    "one zero zero",             # digit-by-digit reading
    "सवा",                       # modifier with no number
])
def test_word_amounts_marks_unreadable_phrases(text):
    assert None in numwords.word_amounts(text)
    assert numwords.amounts_in_words(text) == []


def test_word_amounts_keeps_fractions_exact():
    assert numwords.word_amounts("साढ़े तीन") == [Fraction(7, 2)]
    assert numwords.amounts_in_words("साढ़े तीन") == []


# --- soundbox ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("transcript,amount,provider", [
    ("Received rupees sixty", 60, None),
    ("₹1,250 received on Paytm", 1250, "Paytm"),
    ("PhonePe par ₹60 prapt hue", 60, "PhonePe"),
    ("पेटीएम पर साठ रुपये प्राप्त हुए", 60, "Paytm"),
    ("Paytm पर ₹60 प्राप्त हुए", 60, "Paytm"),
    ("அறுபது ரூபாய் பெறப்பட்டது", 60, None),
    ("போன்பே மூலம் ₹120 பெறப்பட்டது", 120, "PhonePe"),
])
def test_soundbox_parses(transcript, amount, provider):
    ev = soundbox.parse(transcript)
    assert ev is not None and ev.amount == amount and ev.provider == provider


@pytest.mark.parametrize("transcript,amount,provider", [
    ("Received ₹60, rupees sixty, on Paytm", 60, "Paytm"),        # same amount said twice
    ("पेटीएम पर डेढ़ सौ रुपये प्राप्त हुए", 150, "Paytm"),
    ("Google Pay: ₹1,00,000 received", 100000, "Google Pay"),
    ("₹60.00 received", 60, None),                                # zero paise is still exact
    ("௬௦ ரூபாய் பெறப்பட்டது", 60, None),                           # Tamil digits
    ("நூற்றியிருபது ரூபாய் பெறப்பட்டது", 120, None),
    ("Received ₹60 on PhonePe and Paytm", 60, None),              # two providers: amount ok, provider unknown
])
def test_soundbox_parses_more(transcript, amount, provider):
    ev = soundbox.parse(transcript)
    assert ev is not None and ev.amount == amount and ev.provider == provider


@pytest.mark.parametrize("transcript", [
    "₹60 sent to Kumar",
    "received 60 and 80",
    "hello how are you",
    "₹60.50 received",
    "payment of ₹60 failed",
])
def test_soundbox_rejects(transcript):
    assert soundbox.parse(transcript) is None


@pytest.mark.parametrize("transcript", [
    "",
    "   ",
    "received sixty and eighty",                     # two amounts in words
    "₹60 received, rupees eighty",                   # digits and words disagree
    "₹60 not received",
    "₹60 debited from your account",
    "refund of ₹60 received",
    "received rupees sixty fifty paise",             # paise in words
    "received 60,80",                                # not a valid digit grouping: two amounts
    "received ₹0",
    "received one zero zero",                        # digit-by-digit reading
    "पेटीएम पर साढ़े तीन रुपये प्राप्त हुए",            # fractional rupees
    "₹60 प्राप्त नहीं हुए",
    "₹60 भेजे गए",
    "₹60 பெறப்படவில்லை",                               # not received
    "₹60 அனுப்பப்பட்டது",                              # sent
    "நூற்றி இருபத்தைஞ்சு ரூபாய் பெறப்பட்டது",           # unreadable number: no partial 100
    "Paytm payment request of ₹60",                  # a request is not a receipt
])
def test_soundbox_rejects_more(transcript):
    assert soundbox.parse(transcript) is None


def test_spoken_tamil_120_and_1200_are_read_right():
    """Bug sweep: colloquial "நூத்தி" (100) was skipped, and "ரத்து" (cancelled) matched inside "ஆயிரத்து"."""
    assert soundbox.parse("நூத்தி இருபது ரூபாய் பெறப்பட்டது").amount == 120
    assert soundbox.parse("ஆயிரத்து இருநூறு ரூபாய் பெறப்பட்டது").amount == 1200
    assert soundbox.parse("அறுபது ரூபாய் ரத்து செய்யப்பட்டது") is None    # a real cancellation still refuses


@pytest.mark.parametrize("heard", [
    "भैया, साठ रुपये मिले?", "60 rupaye mile kya?", "sixty rupees received?", "my phone says sixty rupees received",
    "भैया साठ रुपये जमा कर दिए", "அண்ணா அறுபது ரூபாய் கிடைத்தது?",
])
def test_a_customers_question_or_phone_screen_is_never_soundbox_proof(heard):
    """Bug sweep: the design says a customer's word or screen is never proof of a UPI payment."""
    assert soundbox.parse(heard) is None


@pytest.mark.parametrize("heard, amount", [
    ("Received rupees sixty", 60),
    ("पेटीएम पर साठ रुपये प्राप्त हुए", 60), ("அறுபது ரூபாய் பெறப்பட்டது", 60),
    ("Payment of rupees one hundred and twenty received on Google Pay", 120),
    ("received sixty rupees on phone pe", 60),
])
def test_real_soundbox_lines_still_confirm(heard, amount):
    assert soundbox.parse(heard).amount == amount
