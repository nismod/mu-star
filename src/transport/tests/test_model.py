from transport.model import SampleModel


def test_example():
    model = SampleModel()
    network = None
    disruptions = None
    actual = model.simulate(network, disruptions)
    expected = None
    assert actual == expected
