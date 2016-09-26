import ase.gui.ui as ui
from ase.utils import rotate, irotate


class Rotate:
    update = True

    def __init__(self, gui):
        ui.Window.__init__(self)
        angles = irotate(gui.axes)
        self.set_title(_('Rotate'))
        vbox = ui.VBox()
        pack(vbox, ui.Label(_('Rotation angles:')))
        self.rotate = [ui.Adjustment(value=a, lower=-360, upper=360,
                                      step_incr=1, page_incr=10)
                       for a in angles]
        pack(vbox, [ui.SpinButton(a, climb_rate=0, digits=1)
                    for a in self.rotate])
        for r in self.rotate:
            r.connect('value-changed', self.change)
        button = pack(vbox, ui.Button(_('Update')))
        button.connect('clicked', self.update_angles)
        pack(vbox, ui.Label(_('Note:\nYou can rotate freely\n'
                               'with the mouse, by holding\n'
                               'down mouse button 2.')))
        self.add(vbox)
        vbox.show()
        self.show()
        self.gui = gui

    def change(self, adjustment):
        if self.update:
            x, y, z = [float(a.value) for a in self.rotate]
            self.gui.axes = rotate('%fx,%fy,%fz' % (x, y, z))
            self.gui.set_coordinates()
        return True

    def update_angles(self, button):
        angles = irotate(self.gui.axes)
        self.update = False
        for r, a in zip(self.rotate, angles):
            r.value = a
        self.update = True
