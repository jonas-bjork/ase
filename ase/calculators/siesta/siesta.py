from __future__ import print_function
"""This module defines an ASE interface to SIESTA.

http://www.uam.es/departamentos/ciencias/fismateriac/siesta
"""
import os
from ase.atoms import Atoms
import gzip
from os.path import join, isfile, islink
import string
import numpy as np
from collections import OrderedDict

from ase.units import Ry, eV
from ase.data import atomic_numbers
from ase.io.siesta import read_rho
from ase.calculators.calculator import FileIOCalculator, ReadError
from ase.calculators.calculator import LockedParameters

meV = 0.001 * eV
ALLOWED_BASIS_NAMES = ['SZ', 'SZP', 'DZ', 'DZP']
ALLOWED_SPINS = ['UNPOLARIZED', 'COLLINEAR', 'FULL']
ALLOWED_AUTHORS = {
    'LDA': ['PZ', 'CA', 'PW92'],
    'GGA': ['PW91', 'PBE', 'revPBE', 'RPBE',
            'WC', 'AM05', 'PBEsol', 'PBEJsJrLO',
            'PBEGcGxLO', 'PBEGcGxHEG', 'BLYP',
            ],
    'VDW': ['DRSLL', 'LMKLL', 'KBM', 'C09', 'BH', 'VV'],
}

Bohr2Ang = 0.529177

def read_xv_file(filename, read_velocity=False):
    "Returns tuple (vectors,speciesnumber,atomnumber,xyz,[v,]) from an XV-file"
    factor = Bohr2Ang
    try:
        file = open(filename, 'r')
    except:
        file = gzip.open(filename + '.gz', 'r')

    # Read cell vectors (lines 1-3)
    vectors = []
    for i in range(3):
        data = string.split(file.readline())
        vectors.append([string.atof(data[j]) * factor for j in range(3)])

    # Read number of atoms (line 4)
    string.atoi(string.split(file.readline())[0])

    # Read remaining lines
    speciesnumber, atomnumber, xyz, V = [], [], [], []
    for line in file.readlines():
        if len(line) > 5:  # Ignore blank lines
            data = string.split(line)
            speciesnumber.append(string.atoi(data[0]))
            atomnumber.append(string.atoi(data[1]))
            xyz.append([string.atof(data[2 + j]) * factor for j in range(3)])
            V.append([string.atof(data[5 + j]) * factor for j in range(3)])
    file.close()

    if read_velocity:
        return vectors, speciesnumber, atomnumber, xyz, V
    else:
        return vectors, speciesnumber, atomnumber, xyz


def xv_to_atoms(filename):
    result = read_xv_file(filename=filename, read_velocity=False)
    unit_cell = np.array(result[0])
    numbers = np.array(result[2])
    positions = np.array(result[3])
    atoms = Atoms(numbers=numbers, positions=positions, cell=unit_cell)

    return atoms

class Shell(LockedParameters):
    def __init__(
            self,
            n=None,
            l=0,
            nzeta=2,
            nzetapol=None,
            split_norm=None,
            soft_confinement='Sankey',
            soft_confinement_prefactor=0.0,
            soft_confinement_inner_radius=0.0,
            rcs=5.0,
            scale_factors=1.0,
            ):
        kwargs = locals()
        kwargs.pop('self')
        LockedParameters.__init__(self, **kwargs)
        self.check_values()

    def check_values(self):
        assert self['soft_confinement'] in ['Sankey', 'Junquera']

    def script(self):
        script = ''
        if not self['n'] is None:
            script += 'n=%d ' % self['n']
        script = '%d %d ' % (self['l'], self['nzeta'])

        if not self['split_norm'] is None:
            script += 'S %.2f ' % self['split_norm']
        if not self['nzetapol'] is None:
            script += 'P %d ' % self['nzetapol']

        if self['soft_confinement'] == 'Sankey':
            pass
        elif self['soft_confinement'] == 'Junquera':
            script += 'E '

        if not self['soft_confinement_prefactor'] is None:
            script += '%.2f ' % self['soft_confinement_prefactor']

        if not self['soft_confinement_inner_radius'] is None:
            radius = self['soft_confinement_inner_radius']
            script += '%.2f ' % radius

        script += '\n'
        rcs = self['rcs']
        if isinstance(rcs, (float, int)):
            rcs = [rcs]*self['nzeta']
        assert len(rcs) == self['nzeta']
        script += ' '.join(['%.2f' % rc for rc in rcs])
        script += '\n'

        sfs = self['scale_factors']
        if isinstance(sfs, (float, int)):
            sfs = [sfs]*self['nzeta']
        assert len(sfs) == self['nzeta']
        script += ' '.join(['%.2f' % sf for sf in sfs])

        return script

class BasisSet(LockedParameters):
    def __init__(
            self,
            basis_type=None,
            ionic_charge=None,
            shells=tuple()):
        kwargs = locals()
        kwargs.pop('self')
        LockedParameters.__init__(self, **kwargs)

    def script(self, label):
        script = label + ' '
        script += "%d " % len(self['shells'])
        if not self['basis_type'] is None:
            script += '%s ' % self['basis_type']
        if not self['ionic_charge'] is None:
            script += '%.2f ' % self['ionic_charge']
        script += '\n'
        shell_scripts = []
        for shell in self['shells']:
            shell_scripts.append(shell.script())

        script += '\n'.join(shell_scripts)

        return script



class SiestaParameters(LockedParameters):
    """
    Parameter class which can write out its own fdf-script.
    """
    def write_fdf(self, f):
        for key, value in self.iteritems():
            key = self.prefix() + '.' + key
            f.write(format_fdf(key, value))


class SolutionMethod(SiestaParameters):
    """
    Collection of parameters related to a specific solution method.
    """
    def identitier(self):
        """
        The string which begins all fdf-keywords in the group.
        """
        raise NotImplementedError

    def write_fdf(self, f):
        """
        Write the SolutionMethod keyword to the fdf-script as well as all
        parameters in this group.
        """
        f.write(format_fdf('SolutionMethod', self.identifier()))
        SiestaParameters.write_fdf(self, f)


class Diag(SolutionMethod):
    """
    Parameters related to the diagonalization solution method.
    """
    def prefix(self):
        return 'Diag'

    def identifier(self):
        return 'diagon'

    def __init__(
            self,
            DivideAndConquer=False,
            AllInOne=False,
            NoExpert=False,
            PreRotate=False,
            Use2D=False,
            Memory=1.0,
            ParallelOverK=False, ):
        kwargs = locals()
        kwargs.pop('self')
        SolutionMethod.__init__(self, **kwargs)


class OrderN(SolutionMethod):
    """
    Parameters related to the OrderN solution method.
    """
    def prefix(self):
        return 'ON'

    def identifier(self):
        return 'ON'

    def __init__(
            self,
            functional='Kim',
            MaxNumIter=1000,
            etol=1e-8,
            eta='0.0 eV',
            eta_alpha='0.0 eV',
            eta_beta='0.0 eV',
            RcLWF='9.5 Bohr',
            ChemicalPotential=False,
            ChemicalPotentialUse=False,
            ChemicalPotentialRc='9.5 Bohr',
            ChemicalPotentialTemperature='0.05 Ry',
            ChemicalPotentialOrder=100,
            LowerMemory=False,
            UseSaveLWF=False,
            OccupationFunction='FD',
            OccupationMPOrder=1, ):

        kwargs = locals()
        kwargs.pop('self')
        SolutionMethod.__init__(self, **kwargs)


class Specie(LockedParameters):
    """
    Parameters for specifying the behaviour for a single species in the
    calculation. If the tag argument is set to an integer then atoms with
    the specified element and tag will be a seperate species.

    Pseudopotential and basis set can be specified. Additionally the species
    can be set be a ghost species, meaning that they will not be considered
    atoms, but the corresponding basis set will be used.
    """
    def __init__(self,
                 symbol,
                 basis_set='DZP',
                 pseudopotential=None,
                 tag=None,
                 ghost=False,
                 ):
        kwargs = locals()
        kwargs.pop('self')
        LockedParameters.__init__(self, **kwargs)


class Siesta(FileIOCalculator):
    """
    Calculator interface to the SIESTA code.
    """
    implemented_properties = tuple([
        'energy',
        'forces',
        'stress',
        'dipole',
        'eigenvalues',
        'density',
        'fermi_energy',
    ])

    # Dictionary of valid input vaiables.
    # Any additional variables will be written directly as to fdf-files.
    # Replace '.' with '_' in arguments.
    default_parameters = LockedParameters(
        label='siesta',
        mesh_cutoff=200 * Ry,
        energy_shift=100 * meV,
        kpts=(1, 1, 1),
        atoms=None,
        xc='LDA',
        species=tuple(),
        basis_set='DZP',
        spin='COLLINEAR',
        solution_method=Diag(),
        pseudo_qualifier=None,
        pseudo_path=None,
        n_nodes=1,
        restart=None,
        ignore_bad_restart_file=False,
    )

    def __init__(self, **kwargs):
        """
        ASE interface to the SIESTA code.

        Parameters:
            -label        : The base head of all created files.
            -mesh_cutoff  : tuple of (value, energy_unit)
                            The mesh cutoff energy for determining number of
                            grid points.
            -xc
            -energy_shift : tuple of (value, energy_unit)
                            The confining energy of the basis sets.
            -kpts         : Tuple of 3 integers, the k-points in different
                            directions.
            -atoms        : The Atoms object.
            -species      : None|list of Specie objects. The species objects
                            can be used to to specify the basis set,
                            pseudopotential and whether the species is ghost.
                            The tag on the atoms object and the element is used
                            together to identify the species.
            -basis_set    : "SZ"|"SZP"|"DZ"|"DZP", strings which specify the
                            type of functions basis set.
            -spin         : "UNPOLARIZED"|"COLLINEAR"|"FULL". The level of spin
                            description to be used.
            -solution_method : Diag object| OrderN object. This is the
                            internal method used by the siesta calculator.
            -pseudo_path  : None|path. This path is where
                            pseudopotentials are taken from.
                            If None is given, then then the path given
                            in $SIESTA_PP_PATH will be used.
            -pseudo_qualifier: None|string. This string will be added to the
                            pseudopotential path that will be retrieved.
                            For hydrogen with qualifier "abc" the
                            pseudopotential "H.abc.psf" will be retrieved.
            -n_nodes      : The number of nodes to use.
            -restart      : str.  Prefix for restart file.
                            May contain a directory.
                            Default is  None, don't restart.
            -ignore_bad_restart_file: bool.
                            Ignore broken or missing restart file.
                            By default, it is an error if the restart
                            file is missing or broken.
                            """
        parameters = self.get_default_parameters()
        parameters.update(kwargs)

        # Setup the siesta command based on number of nodes.
        siesta = os.environ.get('SIESTA')
        label = parameters['label']
        self.label = label
        n_nodes = parameters['n_nodes']
        if n_nodes > 1:
            command = 'mpirun -np %d %s < ./%s.fdf > ./%s.out'
            command = command % (n_nodes, siesta, label, label)
        else:
            command = '%s < ./%s.fdf > ./%s.out' % (siesta, label, label)

        # Call the base class.
        FileIOCalculator.__init__(
            self,
            command=command,
            **parameters
        )

    def __getitem__(self, key):
        """ Convenience method to retrieve a parameter as
            calculator[key] rather than calculator.parameters[key]
        """
        return self.parameters[key]

    def species(self, atoms):
        """
        Find all relevant species depending on the atoms object and
        species input.

        Parameters :
            - atoms : An Atoms object.
        """
        # For each element use default specie from the species input, or set
        # up a default species  from the general default parameters.
        symbols = np.array(atoms.get_chemical_symbols())
        tags = atoms.get_tags()
        species = list(self['species'])
        default_species = [s for s in species if s['tag'] is None]
        default_symbols = [s['symbol'] for s in default_species]
        for symbol in symbols:
            if not symbol in default_symbols:
                specie = Specie(
                    symbol=symbol,
                    basis_set=self['basis_set'],
                    tag=None,
                )
                default_species.append(specie)
                default_symbols.append(symbol)
        assert len(default_species) == len(np.unique(symbols))

        # Set default species as the first species.
        species_numbers = np.zeros(len(atoms), int)
        i = 1
        for specie in default_species:
            mask = symbols == specie['symbol']
            species_numbers[mask] = i
            i += 1

        # Set up the non-default species.
        non_default_species = [s for s in species if not s['tag'] is None]
        for specie in non_default_species:
            mask1 = (tags == specie['tag'])
            mask2 = (symbols == specie['symbol'])
            mask = np.logical_and(mask1, mask2)
            if sum(mask) > 0:
                species_numbers[mask] = i
                i += 1
        all_species = default_species + non_default_species

        return all_species, species_numbers

    def set(self, **kwargs):
        """
        Set all parameters.
        """
        # Check energy inputs.
        for arg in ['mesh_cutoff', 'energy_shift']:
            value = kwargs.get(arg)
            if not (isinstance(value, (float, int)) and value > 0):
                mess = "'%s' must be a positive number(in eV), \
                    got '%s'" % (arg, value)
                raise ValueError(mess)

        # Check the basis set input.
        basis_set = kwargs.get('basis_set')
        allowed = ALLOWED_BASIS_NAMES
        if not (isinstance(basis_set, BasisSet) or basis_set in allowed):
            mess = "Basis must be either %s, got %s" % (allowed, basis_set)
            raise Exception(mess)

        # Check the spin input.
        spin = kwargs.get('spin')
        if not spin is None and (not spin in ALLOWED_SPINS):
            raise Exception("Spin must be %s, got %s" % (ALLOWED_SPINS, spin))

        # Check the functional input.
        xc = kwargs.get('xc')

        if xc in ALLOWED_AUTHORS.keys():
            functional = xc
            authors = ALLOWED_AUTHORS[xc][0]
        else:
            found = False
            for key, value in ALLOWED_AUTHORS.iteritems():
                if xc in value:
                    found = True
                    functional = key
                    authors = xc
                    break

            if not found:
                raise ValueError("Unrecognized 'xc' keyword: '%s'" % xc)
        kwargs['xc'] = (functional, authors)


        FileIOCalculator.set(self, **kwargs)

    def write_input(self, atoms, properties=None, system_changes=None):
        """
        Write input (fdf)-file.
        """
        # Call base calculator.
        FileIOCalculator.write_input(
            self,
            atoms=atoms,
            properties=properties,
            system_changes=system_changes,
        )
        if system_changes is None and properties is None:
            return

        filename = self.label + '.fdf'

        # On any changes, remove all analysis files.
        if not system_changes is None:
            self.remove_analysis()

        # Start writing the file.
        with open(filename, 'w') as f:
            # Use the saved density matrix if only 'cell' and 'positions'
            # haved changes.
            if system_changes is None or \
                (not 'numbers' in system_changes and
                 not 'initial_magmoms' in system_changes and
                 not 'initial_charges' in system_changes):
                f.write(format_fdf('DM.UseSaveDM', True))

            # Save density.
            if 'density' in properties:
                f.write(format_fdf('SaveRho', True))

            # Write system name and label.
            f.write(format_fdf('SystemName', self.label))
            f.write(format_fdf('SystemLabel', self.label))

            # Write solution method and all parameters.
            self['solution_method'].write_fdf(f)

            # Write the rest.
            self._write_species(f, atoms)
            self._write_kpts(f)
            self._write_structure(f, atoms)
            self._write_fdf_arguments(f)

    def read(self, restart):
        if not os.path.exists(restart):
            raise ReadError("The restart file '%s' does not exist" % restart)
        self.atoms = xv_to_atoms(restart)
        self.read_results()

    def _write_fdf_arguments(self, f):
        """
        Write all arguments not given as default directly as fdf-format.
        """
        d_parameters = self.get_default_parameters()
        for key, value in self.parameters.iteritems():
            if not key in d_parameters.keys():
                f.write(format_fdf(key, value))

    def remove_analysis(self):
        """ Remove all analysis files"""
        filename = self.label + '.RHO'
        if os.path.exists(filename):
            os.remove(filename)

    def _write_structure(self, f, atoms):
        """
        Translate the Atoms object to fdf-format.

        Parameters:
            - f:     An open file object.
            - atoms: An atoms object.
        """
        unit_cell = atoms.get_cell()
        xyz = atoms.get_positions()
        f.write('\n')
        f.write(format_fdf('NumberOfAtoms', len(xyz)))
        f.write(format_fdf('LatticeConstant', '1.0 Ang'))
        f.write('%block LatticeVectors\n')
        for i in range(3):
            for j in range(3):
                f.write(string.rjust('%.15f' % unit_cell[i, j], 16) + ' ')
            f.write('\n')
        f.write('%endblock LatticeVectors\n')

        self._write_atomic_coordinates(f, atoms)

        # Write magnetic moments.
        magmoms = atoms.get_initial_magnetic_moments()
        f.write('%block DM.InitSpin\n')
        for n, M in enumerate(magmoms):
            if M != 0:
                f.write('%d %.14f\n' % (n + 1, M))
        f.write('%endblock DM.InitSpin\n')

    def _write_atomic_coordinates(self, f, atoms):
        """
        Write atomic coordinates.

        Parameters:
            - f:     An open file object.
            - atoms: An atoms object.
        """
        species, species_numbers = self.species(atoms)
        f.write('AtomicCoordinatesFormat  Ang\n')
        f.write('%block AtomicCoordinatesAndAtomicSpecies\n')
        for atom, number in zip(atoms, species_numbers):
            xyz = atom.position
            line = string.rjust('%.9f' % xyz[0], 16) + ' '
            line += string.rjust('%.9f' % xyz[1], 16) + ' '
            line += string.rjust('%.9f' % xyz[2], 16) + ' '
            line += str(number) + '\n'
            f.write(line)
        f.write('%endblock AtomicCoordinatesAndAtomicSpecies\n')

        origin = tuple(-atoms.get_celldisp().flatten())
        f.write('%block AtomicCoordinatesOrigin\n')
        f.write('%.4f  %.4f  %.4f\n' % origin)
        f.write('%endblock AtomicCoordinatesOrigin\n')

    def _write_kpts(self, f):
        """
        Write kpts.

        Parameters:
            - f : Open filename.
        """
        kpts = np.array(self['kpts'])
        f.write('\n')
        f.write('#KPoint grid\n')
        f.write('%block kgrid_Monkhorst_Pack\n')

        for i in range(3):
            s = ''
            if i < len(kpts):
                number = kpts[i]
                displace = 0.0
            else:
                number = 1
                displace = 0
            for j in range(3):
                if j == i:
                    write_this = number
                else:
                    write_this = 0
                s += '%d  ' % write_this
            s += '%1.1f\n' % displace
            f.write(s)
        f.write('%endblock kgrid_Monkhorst_Pack\n')
        f.write('\n')


    def _write_species(self, f, atoms):
        """
        Write input related the different species.

        Parameters:
            - f:     An open file object.
            - atoms: An atoms object.
        """
        energy_shift = '%.4f eV' % self['energy_shift']
        f.write(format_fdf('PAO_EnergyShift', energy_shift))
        mesh_cutoff = '%.4f eV' % self['mesh_cutoff']
        f.write(format_fdf('MeshCutoff', mesh_cutoff))

        species, species_numbers = self.species(atoms)
        if self['spin'] == 'UNPOLARIZED':
            f.write(format_fdf('SpinPolarized', False))
        elif self['spin'] == 'COLLINEAR':
            f.write(format_fdf('SpinPolarized', True))
        elif self['spin'] == 'FULL':
            f.write(format_fdf('SpinPolarized', True))
            f.write(format_fdf('NonCollinearSpin', True))

        functional, authors = self.parameters['xc']
        f.write(format_fdf('XC_functional', functional))
        if not authors is None:
            f.write(format_fdf('XC_authors', authors))

        if not self['pseudo_path'] is None:
            pseudo_path = self['pseudo_path']
        elif 'SIESTA_PP_PATH' in os.environ:
            pseudo_path = os.environ['SIESTA_PP_PATH']
        else:
            mess = "Please set the environment variable 'SIESTA_PP_PATH'"
            raise Exception(mess)

        f.write(format_fdf('NumberOfSpecies', len(species)))

        labels = []
        pao_basis = []
        chemical_labels = []
        basis_sizes = []
        for species_number, specie in enumerate(species):
            species_number += 1
            symbol = specie['symbol']
            atomic_number = atomic_numbers[symbol]

            if specie['pseudopotential'] is None:
                label = '.'.join([symbol, self.pseudo_qualifier()])
                pseudopotential = label + '.psf'
            else:
                pseudopotential = specie['pseudopotential']
                label = os.path.basename(pseudopotential)
                label = '.'.join(label.split('.')[:-1])

            if not os.path.isabs(pseudopotential):
                pseudopotential = join(pseudo_path, pseudopotential)

            if not os.path.exists(pseudopotential):
                mess = "Pseudopotential '%s' not found" % pseudopotential
                raise RuntimeError(mess)

            name = os.path.basename(pseudopotential)
            name = name.split('.')
            name.insert(-1, str(species_number))
            if specie['ghost']:
                name.insert(-1, 'ghost')
                atomic_number = -atomic_number
            name = '.'.join(name)

            if join(os.getcwd(), name) != pseudopotential:
                if islink(name) or isfile(name):
                    os.remove(name)
                os.symlink(pseudopotential, name)

            label = '.'.join(np.array(name.split('.'))[:-1])
            chemical_labels.append('%d %d %s' % (species_number, atomic_number, label))
            if isinstance(specie['basis_set'], BasisSet):
                pao_basis.append(specie['basis_set'].script(label))
            else:
                basis_sizes.append((label, specie['basis_set']))
        f.write((format_fdf('ChemicalSpecieslabel', chemical_labels)))
        f.write((format_fdf('PAO.Basis', pao_basis)))
        f.write((format_fdf('PAO.BasisSizes', basis_sizes)))

    def pseudo_qualifier(self):
        """
        Get the extra string used in the middle of the pseudopotential.
        The retrieved pseudopotential for a specific element will be
        'H.xxx.psf' for the element 'H' with qualifier 'xxx'. If qualifier
        is set to None then the qualifier is set to functional name.
        """
        if self['pseudo_qualifier'] is None:
            return self['xc'][0].lower()
        else:
            return self['pseudo_qualifier']

    def read_results(self):
        """
        Read the results.
        """
        self.read_energy()
        self.read_forces_stress()
        self.read_eigenvalues()
        self.read_dipole()
        self.read_pseudo_density()

    def read_pseudo_density(self):
        """
        Read the density if it is there.
        """
        filename = self.label + '.RHO'
        if isfile(filename):
            self.results['density'] = read_rho(filename)

    def read_energy(self):
        """
        Read energy from SIESTA's text-output file.
        """
        with open(self.label + '.out', 'r') as f:
            text = f.read().lower()

        assert 'error' not in text
        lines = iter(text.split('\n'))

        # Get the number of grid points used:
        for line in lines:
            if line.startswith('initmesh: mesh ='):
                n_points = [int(word) for word in line.split()[3:8:2]]
                self.results['n_grid_point'] = n_points
                break

        for line in lines:
            if line.startswith('siesta: etot    ='):
                self.results['energy'] = float(line.split()[-1])
                line = lines.next()
                self.results['free_energy'] = float(line.split()[-1])
                break
        else:
            raise RuntimeError

    def read_forces_stress(self):
        """
        Read the forces and stress from the FORCE_STRESS file.
        """
        with open('FORCE_STRESS', 'r') as f:
            lines = f.readlines()

        stress_lines = lines[1:4]
        stress = np.empty((3, 3))
        for i in range(3):
            line = stress_lines[i].strip().split(' ')
            line = [s for s in line if len(s) > 0]
            stress[i] = map(float, line)

        self.results['stress'] = np.array(
            [stress[0, 0], stress[1, 1], stress[2, 2],
             stress[1, 2], stress[0, 2], stress[0, 1]])

        start = 5
        self.results['forces'] = np.zeros((len(lines) - start, 3), float)
        for i in range(start, len(lines)):
            line = [s for s in lines[i].strip().split(' ') if len(s) > 0]
            self.results['forces'][i - start] = map(float, line[2:5])

    def read_eigenvalues(self):
        """
        Read eigenvalues from the '.EIG' file.
        This is done pr. kpoint.
        """
        assert os.access(self.label + '.EIG', os.F_OK)
        assert os.access(self.label + '.KP', os.F_OK)

        # Read k point weights
        text = open(self.label + '.KP', 'r').read()
        lines = text.split('\n')
        n_kpts = int(lines[0].strip())
        self.weights = np.zeros((n_kpts,))
        for i in range(n_kpts):
            l = lines[i + 1].split()
            self.weights[i] = float(l[4])

        # Read eigenvalues and fermi-level
        with open(self.label + '.EIG', 'r') as f:
            text = f.read()
        lines = text.split('\n')
        e_fermi = float(lines[0].split()[0])
        tmp = lines[1].split()
        self.n_bands = int(tmp[0])
        n_spin_bands = int(tmp[1])
        self.spin_pol = n_spin_bands == 2
        lines = lines[2:-1]
        lines_per_kpt = (self.n_bands * n_spin_bands / 10 +
                         int((self.n_bands * n_spin_bands) % 10 != 0))
        eig = OrderedDict()
        for i in range(len(self.weights)):
            tmp = lines[i * lines_per_kpt:(i + 1) * lines_per_kpt]
            v = [float(v) for v in tmp[0].split()[1:]]
            for l in tmp[1:]:
                v.extend([float(t) for t in l.split()])
            if self.spin_pol:
                eig[(i, 0)] = np.array(v[0:self.n_bands])
                eig[(i, 1)] = np.array(v[self.n_bands:])
            else:
                eig[(i, 0)] = np.array(v)

        self.results['fermi_energy'] = e_fermi
        self.results['eigenvalues'] = eig

    def read_dipole(self):
        """
        Read dipole moment.
        """
        dipole = np.zeros([1, 3])
        with open(self.label + '.out', 'r') as f:
            for line in f:
                if line.rfind('Electric dipole (Debye)') > -1:
                    dipole = np.array([float(f) for f in line.split()[5:8]])

        # debye to e*Ang
        self.results['dipole'] = dipole * 0.2081943482534


def format_fdf(key, value):
    """
    Write an fdf key-word value pair.

    Parameters:
        - key   : The fdf-key
        - value : The fdf value.
    """
    if isinstance(value, (list, tuple)) and len(value) == 0:
        return ''

    key = format_key(key)
    new_value = format_value(value)

    if isinstance(value, (list, tuple)):
        return '%block ' + key + '\n' + new_value + '\n' + '%endblock ' + key + '\n'
    else:
        return '%s  %s\n' % (key, new_value)


def format_value(value):
    """
    Format python values to fdf-format.

    Parameters:
        - value : The value to format.
    """
    if isinstance(value, tuple):
        sub_values = map(format_value, value)
        value = '\t'.join(sub_values)
    elif isinstance(value, list):
        sub_values = map(format_value, value)
        value = '\n'.join(sub_values)
    else:
        value = str(value)

    return value


def format_key(key):
    """ Fix the fdf-key replacing '_' with '.' """
    return key.replace('_', '.')
