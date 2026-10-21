import numpy as np

from motor.color import color_mas_cercano, hex_a_rgb, rgb_a_hex, rgb_a_lab


def test_hex_ida_y_vuelta():
    assert hex_a_rgb("#1a2B3c") == (26, 43, 60)
    assert rgb_a_hex((26, 43, 60)) == "#1a2b3c"


def test_lab_de_referencia():
    lab = rgb_a_lab(np.array([[255, 255, 255], [0, 0, 0]]))
    assert np.allclose(lab[0], [100, 0, 0], atol=0.5)
    assert np.allclose(lab[1], [0, 0, 0], atol=0.5)


def test_color_mas_cercano():
    paleta = rgb_a_lab(np.array([[0, 0, 0], [255, 0, 0], [255, 255, 255]]))
    pixeles = rgb_a_lab(np.array([[[10, 10, 10], [240, 20, 20], [250, 250, 245]]]))
    assert color_mas_cercano(pixeles, paleta).tolist() == [[0, 1, 2]]

