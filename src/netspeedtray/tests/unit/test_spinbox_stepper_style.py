"""Regression guards for #169 ("Plan speed" spinbox: down arrow works, up arrow doesn't).

Root cause: the settings stylesheet gave QSpinBox a border + padding but defined no
::up-button/::down-button geometry, so Qt laid the native steppers under the styled edit
field's right edge. The up-button ended up (partly) beneath the edit field and its clicks
were swallowed, while the down-button (further right / lower) still worked. The fix reserves
explicit, fixed-width stepper buttons so the edit field can never overlap them.
"""
from PyQt6.QtWidgets import QSpinBox, QStyle, QStyleOptionSpinBox

from netspeedtray.utils.styles import dialog_style


def test_dialog_style_defines_spinbox_stepper_geometry(q_app):
    """The QSS must explicitly define both stepper buttons with a reserved width (deterministic,
    style-independent) - deleting this reintroduces the swallowed-up-button bug."""
    qss = dialog_style()
    for sel in ("QSpinBox::up-button", "QSpinBox::down-button",
                "QSpinBox::up-arrow", "QSpinBox::down-arrow"):
        assert sel in qss, f"missing {sel} in dialog_style()"
    up_block = qss.split("QSpinBox::up-button", 1)[1].split("}", 1)[0]
    assert "width:" in up_block, "up-button must reserve a fixed width"


def test_spinbox_edit_field_does_not_overlap_up_button(q_app):
    """With the dialog stylesheet applied, the edit field must not extend into the up-button
    sub-control (the geometric root of #169). Both button rects must be hittable (non-degenerate)."""
    s = QSpinBox()
    s.setRange(0, 100000)
    s.setStyleSheet(dialog_style())
    s.resize(180, 32)
    opt = QStyleOptionSpinBox()
    try:
        s.initStyleOption(opt)
    except Exception:
        pass
    opt.rect = s.rect()
    st = s.style()

    def sub(sc):
        return st.subControlRect(QStyle.ComplexControl.CC_SpinBox, opt, sc, s)

    up = sub(QStyle.SubControl.SC_SpinBoxUp)
    dn = sub(QStyle.SubControl.SC_SpinBoxDown)
    ed = sub(QStyle.SubControl.SC_SpinBoxEditField)

    assert up.width() > 0 and up.height() > 0, f"up-button rect degenerate: {up}"
    assert dn.width() > 0 and dn.height() > 0, f"down-button rect degenerate: {dn}"
    # the edit field's right edge must stay left of the up-button (+1px tolerance for rounding)
    assert ed.x() + ed.width() <= up.x() + 1, (
        f"edit field ({ed.x()}..{ed.x()+ed.width()}) overlaps up-button (starts {up.x()})"
    )
