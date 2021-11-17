import os
import numpy as np
from ase.atoms import Atoms
from ase.utils import reader, writer


def prepare_dftb_input(outfile, atoms, parameters, directory):
    """ Write the innput file for the dftb+ calculation.
        Geometry is taken always from the file 'geo_end.gen'.
    """

    outfile.write('Geometry = GenFormat { \n')
    outfile.write('    <<< "geo_end.gen" \n')
    outfile.write('} \n')
    outfile.write(' \n')

    params = parameters.copy()
    slako_dir = params.pop('slako_dir') if 'slako_dir' in params else ''
    pcpot = params.pop('pcpot') if 'pcpot' in params else None
    do_forces = params.pop('do_forces') if 'do_forces' in params else False

    s = 'Hamiltonian_MaxAngularMomentum_'
    for key in params:
        if key.startswith(s) and len(key) > len(s):
            break
    else:
        # User didn't specify max angular mometa.  Get them from
        # the .skf files:
        symbols = set(atoms.get_chemical_symbols())
        for symbol in symbols:
            path = os.path.join(slako_dir,
                                '{0}-{0}.skf'.format(symbol))
            l = read_max_angular_momentum(path)
            params[s + symbol] = '"{}"'.format('spdf'[l])

    # --------MAIN KEYWORDS-------
    previous_key = 'dummy_'
    myspace = ' '
    for key, value in sorted(params.items()):
        current_depth = key.rstrip('_').count('_')
        previous_depth = previous_key.rstrip('_').count('_')
        for my_backsclash in reversed(
                range(previous_depth - current_depth)):
            outfile.write(3 * (1 + my_backsclash) * myspace + '} \n')
        outfile.write(3 * current_depth * myspace)
        if key.endswith('_') and len(value) > 0:
            outfile.write(key.rstrip('_').rsplit('_')[-1] +
                          ' = ' + str(value) + '{ \n')
        elif (key.endswith('_') and (len(value) == 0)
              and current_depth == 0):  # E.g. 'Options {'
            outfile.write(key.rstrip('_').rsplit('_')[-1] +
                          ' ' + str(value) + '{ \n')
        elif (key.endswith('_') and (len(value) == 0)
              and current_depth > 0):  # E.g. 'Hamiltonian_Max... = {'
            outfile.write(key.rstrip('_').rsplit('_')[-1] +
                          ' = ' + str(value) + '{ \n')
        elif key.count('_empty') == 1:
            outfile.write(str(value) + ' \n')
        elif ((key == 'Hamiltonian_ReadInitialCharges') and
              (str(value).upper() == 'YES')):
            f1 = os.path.isfile(directory + os.sep + 'charges.dat')
            f2 = os.path.isfile(directory + os.sep + 'charges.bin')
            if not (f1 or f2):
                print('charges.dat or .bin not found, switching off guess')
                value = 'No'
            outfile.write(key.rsplit('_')[-1] + ' = ' + str(value) + ' \n')
        else:
            outfile.write(key.rsplit('_')[-1] + ' = ' + str(value) + ' \n')
        if pcpot is not None and ('DFTB' in str(value)):
            outfile.write('   ElectricField = { \n')
            outfile.write('      PointCharges = { \n')
            outfile.write(
                '         CoordsAndCharges [Angstrom] = DirectRead { \n')
            outfile.write('            Records = ' +
                          str(len(pcpot.mmcharges)) + ' \n')
            outfile.write(
                '            File = "dftb_external_charges.dat" \n')
            outfile.write('         } \n')
            outfile.write('      } \n')
            outfile.write('   } \n')
        previous_key = key
    current_depth = key.rstrip('_').count('_')
    for my_backsclash in reversed(range(current_depth)):
        outfile.write(3 * my_backsclash * myspace + '} \n')
    outfile.write('ParserOptions { \n')
    outfile.write('   IgnoreUnprocessedNodes = Yes  \n')
    outfile.write('} \n')
    if do_forces:
        outfile.write('Analysis { \n')
        outfile.write('   CalculateForces = Yes  \n')
        outfile.write('} \n')

def read_dftb_outputs(directory, label):
    """ all results are read from results.tag file
        It will be destroyed after it is read to avoid
        reading it once again after some runtime error """
    results = {}

    with open(os.path.join(directory, 'results.tag'), 'r') as fd:
        lines = fd.readlines()

    with open(os.path.join(directory, f'{label}_pin.hsd'), 'r') as fd:
        atoms = read_dftb(fd)

    charges, energy, dipole = read_charges_energy_dipole()
    if charges is not None:
        self.results['charges'] = charges
    self.results['energy'] = energy
    if dipole is not None:
        results['dipole'] = dipole
    if self.do_forces:
        forces = self.read_forces()
        self.results['forces'] = forces
    self.mmpositions = None

    # stress stuff begins
    sstring = 'stress'
    have_stress = False
    stress = list()
    for iline, line in enumerate(self.lines):
        if sstring in line:
            have_stress = True
            start = iline + 1
            end = start + 3
            for i in range(start, end):
                cell = [float(x) for x in self.lines[i].split()]
                stress.append(cell)
    if have_stress:
        stress = -np.array(stress) * Hartree / Bohr**3
        self.results['stress'] = stress.flat[[0, 4, 8, 5, 2, 1]]
    # stress stuff ends

    # eigenvalues and fermi levels
    fermi_levels = self.read_fermi_levels()
    if fermi_levels is not None:
        self.results['fermi_levels'] = fermi_levels

    eigenvalues = self.read_eigenvalues()
    if eigenvalues is not None:
        self.results['eigenvalues'] = eigenvalues

    # calculation was carried out with atoms written in write_input
    os.remove(os.path.join(self.directory, 'results.tag'))

def read_max_angular_momentum(path):
    """Read maximum angular momentum from .skf file.

    See dftb.org for A detailed description of the Slater-Koster file format.
    """
    with open(path, 'r') as fd:
        line = fd.readline()
        if line[0] == '@':
            # Extended format
            fd.readline()
            l = 3
            pos = 9
        else:
            # Simple format:
            l = 2
            pos = 7

        # Sometimes there ar commas, sometimes not:
        line = fd.readline().replace(',', ' ')

        occs = [float(f) for f in line.split()[pos:pos + l + 1]]
        for f in occs:
            if f > 0.0:
                return l
            l -= 1

def read_charges_energy_dipole():
    """Get partial charges on atoms
        in case we cannot find charges they are set to None
    """
    with open(os.path.join(directory, 'detailed.out'), 'r') as fd:
        lines = fd.readlines()

    for line in lines:
        if line.strip().startswith('Total energy:'):
            energy = float(line.split()[2]) * Hartree
            break

    qm_charges = []
    for n, line in enumerate(lines):
        if ('Atom' and 'Charge' in line):
            chargestart = n + 1
            break
    else:
        # print('Warning: did not find DFTB-charges')
        # print('This is ok if flag SCC=No')
        return None, energy, None

    lines1 = lines[chargestart:(chargestart + len(self.atoms))]
    for line in lines1:
        qm_charges.append(float(line.split()[-1]))

    dipole = None
    for line in lines:
        if 'Dipole moment:' in line and 'au' in line:
            words = line.split()
            dipole = np.array(
                [float(w) for w in words[-4:-1]]) * Bohr

    return np.array(qm_charges), energy, dipole





















@reader
def read_dftb(fd):
    """Method to read coordinates from the Geometry section
    of a DFTB+ input file (typically called "dftb_in.hsd").

    As described in the DFTB+ manual, this section can be
    in a number of different formats. This reader supports
    the GEN format and the so-called "explicit" format.

    The "explicit" format is unique to DFTB+ input files.
    The GEN format can also be used in a stand-alone fashion,
    as coordinate files with a `.gen` extension. Reading and
    writing such files is implemented in `ase.io.gen`.
    """
    lines = fd.readlines()

    atoms_pos = []
    atom_symbols = []
    type_names = []
    my_pbc = False
    fractional = False
    mycell = []

    for iline, line in enumerate(lines):
        if line.strip().startswith('#'):
            pass
        elif 'genformat' in line.lower():
            natoms = int(lines[iline + 1].split()[0])
            if lines[iline + 1].split()[1].lower() == 's':
                my_pbc = True
            elif lines[iline + 1].split()[1].lower() == 'f':
                my_pbc = True
                fractional = True

            symbols = lines[iline + 2].split()

            for i in range(natoms):
                index = iline + 3 + i
                aindex = int(lines[index].split()[1]) - 1
                atom_symbols.append(symbols[aindex])

                position = [float(p) for p in lines[index].split()[2:]]
                atoms_pos.append(position)

            if my_pbc:
                for i in range(3):
                    index = iline + 4 + natoms + i
                    cell = [float(c) for c in lines[index].split()]
                    mycell.append(cell)
        else:
            if 'TypeNames' in line:
                col = line.split()
                for i in range(3, len(col) - 1):
                    type_names.append(col[i].strip("\""))
            elif 'Periodic' in line:
                if 'Yes' in line:
                    my_pbc = True
            elif 'LatticeVectors' in line:
                for imycell in range(3):
                    extraline = lines[iline + imycell + 1]
                    cols = extraline.split()
                    mycell.append(
                        [float(cols[0]), float(cols[1]), float(cols[2])])
            else:
                pass

    if not my_pbc:
        mycell = [0.] * 3

    start_reading_coords = False
    stop_reading_coords = False
    for line in lines:
        if line.strip().startswith('#'):
            pass
        else:
            if 'TypesAndCoordinates' in line:
                start_reading_coords = True
            if start_reading_coords:
                if '}' in line:
                    stop_reading_coords = True
            if (start_reading_coords and not stop_reading_coords
                and 'TypesAndCoordinates' not in line):
                typeindexstr, xxx, yyy, zzz = line.split()[:4]
                typeindex = int(typeindexstr)
                symbol = type_names[typeindex - 1]
                atom_symbols.append(symbol)
                atoms_pos.append([float(xxx), float(yyy), float(zzz)])

    if fractional:
        atoms = Atoms(scaled_positions=atoms_pos, symbols=atom_symbols,
                      cell=mycell, pbc=my_pbc)
    elif not fractional:
        atoms = Atoms(positions=atoms_pos, symbols=atom_symbols,
                      cell=mycell, pbc=my_pbc)

    return atoms


def read_dftb_velocities(atoms, filename):
    """Method to read velocities (AA/ps) from DFTB+ output file geo_end.xyz
    """
    from ase.units import second
    # AA/ps -> ase units
    AngdivPs2ASE = 1.0 / (1e-12 * second)

    with open(filename) as fd:
        lines = fd.readlines()

    # remove empty lines
    lines_ok = []
    for line in lines:
        if line.rstrip():
            lines_ok.append(line)

    velocities = []
    natoms = len(atoms)
    last_lines = lines_ok[-natoms:]
    for iline, line in enumerate(last_lines):
        inp = line.split()
        velocities.append([float(inp[5]) * AngdivPs2ASE,
                           float(inp[6]) * AngdivPs2ASE,
                           float(inp[7]) * AngdivPs2ASE])

    atoms.set_velocities(velocities)
    return atoms


@reader
def read_dftb_lattice(fileobj, images=None):
    """Read lattice vectors from MD and return them as a list.

    If a molecules are parsed add them there."""
    if images is not None:
        append = True
        if hasattr(images, 'get_positions'):
            images = [images]
    else:
        append = False

    fileobj.seek(0)
    lattices = []
    for line in fileobj:
        if 'Lattice vectors' in line:
            vec = []
            for i in range(3):  # DFTB+ only supports 3D PBC
                line = fileobj.readline().split()
                try:
                    line = [float(x) for x in line]
                except ValueError:
                    raise ValueError('Lattice vector elements should be of '
                                     'type float.')
                vec.extend(line)
            lattices.append(np.array(vec).reshape((3, 3)))

    if append:
        if len(images) != len(lattices):
            raise ValueError('Length of images given does not match number of '
                             'cell vectors found')

        for i, atoms in enumerate(images):
            atoms.set_cell(lattices[i])
            # DFTB+ only supports 3D PBC
            atoms.set_pbc(True)
        return
    else:
        return lattices


@writer
def write_dftb(fileobj, images):
    """Write structure in GEN format (refer to DFTB+ manual).
       Multiple snapshots are not allowed. """
    from ase.io.gen import write_gen
    write_gen(fileobj, images)


def write_dftb_velocities(atoms, filename):
    """Method to write velocities (in atomic units) from ASE
       to a file to be read by dftb+
    """
    from ase.units import AUT, Bohr
    # ase units -> atomic units
    ASE2au = Bohr / AUT

    with open(filename, 'w') as fd:
        velocities = atoms.get_velocities()
        for velocity in velocities:
            fd.write(' %19.16f %19.16f %19.16f \n'
                     % (velocity[0] / ASE2au,
                        velocity[1] / ASE2au,
                        velocity[2] / ASE2au))
