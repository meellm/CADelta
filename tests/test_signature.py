from cadelta.signature import Signature


def test_signature_rounds_to_four_decimals():
    s = Signature.from_values(1.000049, 2.0, 3.0, 4.0, 5.0, 6)
    assert s.volume == 1.0
    assert s.area == 2.0


def test_signature_is_hashable_and_equal():
    a = Signature.from_values(1.0, 2.0, 3.0, 4.0, 5.0, 6)
    b = Signature.from_values(1.00001, 2.0, 3.0, 4.0, 5.0, 6)
    assert a == b
    assert hash(a) == hash(b)


def test_signature_distinguishes_face_count():
    a = Signature.from_values(1.0, 2.0, 3.0, 4.0, 5.0, 6)
    b = Signature.from_values(1.0, 2.0, 3.0, 4.0, 5.0, 12)
    assert a != b
