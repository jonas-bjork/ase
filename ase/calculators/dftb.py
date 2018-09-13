"""This module defines an ASE interface to DftbPlus

http://http://www.dftb-plus.info//
http://www.dftb.org/

markus.kaukonen@iki.fi

The file 'geom.out.gen' contains the input and output geometry
and it will be updated during the dftb calculations.

If restart == None
                   it is assumed that a new input file 'dftb_hsd.in'
                   will be written by ase using default keywords
                   and the ones given by the user.

If restart != None
                   it is assumed that keywords are in file restart

The keywords are given, for instance, as follows::

    Hamiltonian_SCC ='YES',
    Hamiltonian_SCCTolerance = 1.0E-008,
    Hamiltonian_MaxAngularMomentum = '',
    Hamiltonian_MaxAngularMomentum_O = '"p"',
    Hamiltonian_MaxAngularMomentum_H = '"s"',
    Hamiltonian_InitialCharges_ = '',
    Hamiltonian_InitialCharges_AllAtomCharges_ = '',
    Hamiltonian_InitialCharges_AllAtomCharges_1 = -0.88081627,
    Hamiltonian_InitialCharges_AllAtomCharges_2 = 0.44040813,
    Hamiltonian_InitialCharges_AllAtomCharges_3 = 0.44040813,

"""

import os

import numpy as np

from ase.calculators.calculator import FileIOCalculator, kpts2mp, kpts2ndarray
from ase.units import Hartree, Bohr
from ase.dft.kpoints import get_bandpath


class Dftb(FileIOCalculator):
    """ A dftb+ calculator with ase-FileIOCalculator nomenclature
    """
    if 'DFTB_COMMAND' in os.environ:
        command = os.environ['DFTB_COMMAND'] + ' > PREFIX.out'
    else:
        command = 'dftb+ > PREFIX.out'

    implemented_properties = ['energy', 'forces', 'charges', 'stress']

    def __init__(self, restart=None, ignore_bad_restart_file=False,
                 label='dftb', atoms=None, kpts=None,
                 run_manyDftb_steps=False,
                 **kwargs):
        """Construct a DFTB+ calculator.

        run_manyDftb_steps:  Logical
            True: many steps are run by DFTB+,
            False:a single force&energy calculation at given positions

        kpts: list/tuple or dict
            When a list or tuple of 3 integers is given, 
            k-points will be set according to a Monkhorst-Pack mesh 
            with dimensions given by kpts.

            This keyword can also be a dictionary, for the purpose of 
            band structure calculations. The dict should contain the keys:
              'path': string with the special k-points (for more info,
                      see ase.dft.kpoints.special_points)
              'npoints': the total number of k-points along the path
            e.g. for an FCC lattice: kpts={'path':'GKLGX', 'npoints':100}

        ---------
        Additional object (to be set by function embed)
        pcpot: PointCharge object
            An external point charge potential (only in qmmm)
        """

        from ase.dft.kpoints import monkhorst_pack

        if 'DFTB_PREFIX' in os.environ:
            self.slako_dir = os.environ['DFTB_PREFIX'].rstrip('/') + '/'
        else:
            self.slako_dir = './'

        # to run Dftb as energy and force calculator use
        # Driver_MaxSteps=0,
        if run_manyDftb_steps:
            # minimisation of molecular dynamics is run by native DFTB+
            self.default_parameters = dict(
                Hamiltonian_='DFTB',
                Hamiltonian_SlaterKosterFiles_='Type2FileNames',
                Hamiltonian_SlaterKosterFiles_Prefix=self.slako_dir,
                Hamiltonian_SlaterKosterFiles_Separator='"-"',
                Hamiltonian_SlaterKosterFiles_Suffix='".skf"',
                Hamiltonian_MaxAngularMomentum_='')
        else:
            # using ase to get forces and energy only
            # (single point calculation)
            self.default_parameters = dict(
                Hamiltonian_='DFTB',
                Driver_='ConjugateGradient',
                Driver_MaxForceComponent='1E-4',
                Driver_MaxSteps=0,
                Hamiltonian_SlaterKosterFiles_='Type2FileNames',
                Hamiltonian_SlaterKosterFiles_Prefix=self.slako_dir,
                Hamiltonian_SlaterKosterFiles_Separator='"-"',
                Hamiltonian_SlaterKosterFiles_Suffix='".skf"',
                Hamiltonian_MaxAngularMomentum_='')

        self.pcpot = None
        self.lines = None
        self.atoms = None
        self.atoms_input = None
        self.outfilename = 'dftb.out'

        FileIOCalculator.__init__(self, restart, ignore_bad_restart_file,
                                  label, atoms,
                                  **kwargs)

        # Determine number of spin channels
        try:
            entry = kwargs['Hamiltonian_SpinPolarisation']
            spinpol = 'colinear' in entry.lower()
        except KeyError:
            spinpol = False
        self.nspin = 2 if spinpol else 1

        # kpoint stuff by ase
        self.kpts = kpts
        self.kpts_coord = kpts2ndarray(self.kpts, atoms=atoms)

        if self.kpts is not None:
            initkey = 'Hamiltonian_KPointsAndWeights'
            self.parameters[initkey + '_'] = ''
            if isinstance(self.kpts, dict):
                self.parameters[initkey + '_'] = 'Klines '

            for i, c in enumerate(self.kpts_coord):
                key = initkey + '_empty%09d'  % i
                c_str = ' '.join(map(str, c))
                if isinstance(self.kpts, dict):
                    c_str = '1 ' + c_str
                else:
                    c_str += ' 1.0'
                self.parameters[key] = c_str

    def write_dftb_in(self, filename):
        """ Write the innput file for the dftb+ calculation.
            Geometry is taken always from the file 'geo_end.gen'.
        """

        outfile = open(filename, 'w')
        outfile.write('Geometry = GenFormat { \n')
        outfile.write('    <<< "geo_end.gen" \n')
        outfile.write('} \n')
        outfile.write(' \n')

        params = self.parameters.copy()

        s = 'Hamiltonian_MaxAngularMomentum_'
        for key in params:
            if key.startswith(s) and len(key) > len(s):
                break
        else:
            # User didn't specify max angular mometa.  Get them from
            # the .skf files:
            symbols = set(self.atoms.get_chemical_symbols())
            for symbol in symbols:
                path = os.path.join(self.slako_dir,
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
            if key.endswith('_'):
                outfile.write(key.rstrip('_').rsplit('_')[-1] +
                              ' = ' + str(value) + '{ \n')
            elif key.count('_empty') == 1:
                outfile.write(str(value) + ' \n')
            else:
                outfile.write(key.rsplit('_')[-1] + ' = ' + str(value) + ' \n')
            if self.pcpot is not None and ('DFTB' in str(value)):
                outfile.write('   ElectricField = { \n')
                outfile.write('      PointCharges = { \n')
                outfile.write(
                    '         CoordsAndCharges [Angstrom] = DirectRead { \n')
                outfile.write('            Records = ' +
                              str(len(self.pcpot.mmcharges)) + ' \n')
                outfile.write(
                    '            File = "dftb_external_charges.dat" \n')
                outfile.write('         } \n')
                outfile.write('      } \n')
                outfile.write('   } \n')
            previous_key = key
        current_depth = key.rstrip('_').count('_')
        for my_backsclash in reversed(range(current_depth)):
            outfile.write(3 * my_backsclash * myspace + '} \n')
        # output to 'results.tag' file (which has proper formatting)
        outfile.write('Options { \n')
        outfile.write('   WriteResultsTag = Yes  \n')
        outfile.write('} \n')
        outfile.write('ParserOptions { \n')
        outfile.write('   IgnoreUnprocessedNodes = Yes  \n')
        outfile.write('} \n')

        outfile.close()

    def set(self, **kwargs):
        changed_parameters = FileIOCalculator.set(self, **kwargs)
        if changed_parameters:
            self.reset()
        return changed_parameters

    def check_state(self, atoms):
        system_changes = FileIOCalculator.check_state(self, atoms)
        # Ignore unit cell for molecules:
        if not atoms.pbc.any() and 'cell' in system_changes:
            system_changes.remove('cell')
        if self.pcpot and self.pcpot.mmpositions is not None:
            system_changes.append('positions')
        return system_changes

    def write_input(self, atoms, properties=None, system_changes=None):
        from ase.io import write
        FileIOCalculator.write_input(
            self, atoms, properties, system_changes)
        self.write_dftb_in(os.path.join(self.directory, 'dftb_in.hsd'))
        write(os.path.join(self.directory, 'geo_end.gen'), atoms)
        # self.atoms is none until results are read out,
        # then it is set to the ones at writing input
        self.atoms_input = atoms
        self.atoms = None
        if self.pcpot:
            self.pcpot.write_mmcharges('dftb_external_charges.dat')

    def read_results(self):
        """ all results are read from results.tag file
            It will be destroyed after it is read to avoid
            reading it once again after some runtime error """

        myfile = open(os.path.join(self.directory, 'results.tag'), 'r')
        self.lines = myfile.readlines()
        myfile.close()

        self.atoms = self.atoms_input
        charges, energy = self.read_charges_and_energy()
        if charges is not None:
            self.results['charges'] = charges
        self.results['energy'] = energy
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

    def read_forces(self):
        """Read Forces from dftb output file (results.tag)."""
        from ase.units import Hartree, Bohr

        # Force line indexes
        for iline, line in enumerate(self.lines):
            fstring = 'forces   '
            if line.find(fstring) >= 0:
                index_force_begin = iline + 1
                line1 = line.replace(':', ',')
                index_force_end = iline + 1 + \
                    int(line1.split(',')[-1])
                break
        try:
            gradients = []
            for j in range(index_force_begin, index_force_end):
                word = self.lines[j].split()
                gradients.append([float(word[k]) for k in range(0, 3)])
            return np.array(gradients) * Hartree / Bohr
        except:
            raise RuntimeError('Problem in reading forces')

    def read_charges_and_energy(self):
        """Get partial charges on atoms
            in case we cannot find charges they are set to None
        """
        infile = open(os.path.join(self.directory, 'detailed.out'), 'r')
        lines = infile.readlines()
        infile.close()

        for line in lines:
            if line.strip().startswith('Total energy:'):
                energy = float(line.split()[2]) * Hartree
                break

        qm_charges = []
        for n, line in enumerate(lines):
            if ('Atom' and 'Net charge' in line):
                chargestart = n + 1
                break
        else:
            # print('Warning: did not find DFTB-charges')
            # print('This is ok if flag SCC=NO')
            return None, energy
        lines1 = lines[chargestart:(chargestart + len(self.atoms))]
        for line in lines1:
            qm_charges.append(float(line.split()[-1]))

        return np.array(qm_charges), energy

    def get_charges(self, atoms):
        """ Get the calculated charges
        this is inhereted to atoms object """
        if 'charges' in self.results:
            return self.results['charges']
        else:
            return None

    def read_eigenvalues(self):
        """ Read Eigenvalues from dftb output file (results.tag).
            Unfortunately, the order seems to be scrambled. """
        # Eigenvalue line indexes
        index_eigval_begin = None
        for iline, line in enumerate(self.lines):
            fstring = 'eigenvalues   '
            if line.find(fstring) >= 0:
                index_eigval_begin = iline + 1
                line1 = line.replace(':', ',')
                ncol, nband, nkpt, nspin = map(int, line1.split(',')[-4:])
                rows_per_kpt = int(np.ceil(nband * 1. / ncol))
                break
        else:
            return None

        try:
            index = index_eigval_begin
            eigval = np.zeros((nkpt, nspin, nband)) 
            for i in range(nspin):
                for j in range(nkpt):
                    eigenvalues = []
                    for k in range(rows_per_kpt):
                        eigenvalues += map(float, self.lines[index].split())
                        index += 1
                    eigval[j, i] = eigenvalues
            return eigval * Hartree
        except:
            raise RuntimeError('Problem in reading eigenvalues')

    def read_fermi_levels(self):
        """ Read Fermi level(s) from dftb output file (results.tag). """
        # Fermi level line indexes
        for iline, line in enumerate(self.lines):
            fstring = 'fermi_level   '
            if line.find(fstring) >= 0:
                index_fermi = iline + 1
                break
        else:
            return None

        try:
            fermi_levels = []
            words = self.lines[index_fermi].split()
            assert len(words) == 2
            for word in words:
                e = float(word)
                if abs(e) > 1e-8:
                    # Without spin polarization, one of the Fermi 
                    # levels is equal to 0.000000000000000E+000    
                    fermi_levels.append(e)
            return np.array(fermi_levels) * Hartree
        except:
            raise RuntimeError('Problem in reading Fermi levels')

    def get_ibz_k_points(self):
        return self.kpts_coord.copy()

    def get_number_of_spins(self):
        return self.nspin

    def get_eigenvalues(self, kpt=0, spin=0): 
        return self.results['eigenvalues'][kpt, spin]

    def get_fermi_levels(self):
        return self.results['fermi_levels'].copy()

    def get_fermi_level(self):
        return max(self.get_fermi_levels())

    def embed(self, mmcharges=None, directory='./'):
        """Embed atoms in point-charges (mmcharges)
        """
        self.pcpot = PointChargePotential(mmcharges, self.directory)
        return self.pcpot


class PointChargePotential:
    def __init__(self, mmcharges, directory='./'):
        """Point-charge potential for DFTB+.
        """
        self.mmcharges = mmcharges
        self.directory = directory
        self.mmpositions = None
        self.mmforces = None

    def set_positions(self, mmpositions):
        self.mmpositions = mmpositions

    def set_charges(self, mmcharges):
        self.mmcharges = mmcharges

    def write_mmcharges(self, filename='dftb_external_charges.dat'):
        """ mok all
        write external charges as monopoles for dftb+.

        """
        if self.mmcharges is None:
            print("DFTB: Warning: not writing exernal charges ")
            return
        charge_file = open(os.path.join(self.directory, filename), 'w')
        for [pos, charge] in zip(self.mmpositions, self.mmcharges):
            [x, y, z] = pos
            charge_file.write('%12.6f %12.6f %12.6f %12.6f \n'
                              % (x, y, z, charge))
        charge_file.close()

    def get_forces(self, calc, get_forces=False):
        """ returns forces on point charges if the flag get_forces=True """
        if get_forces:
            return self.read_forces_on_pointcharges()
        else:
            return np.zeros_like(self.mmpositions)

    def read_forces_on_pointcharges(self):
        """Read Forces from dftb output file (results.tag)."""
        from ase.units import Hartree, Bohr
        infile = open(os.path.join(self.directory, 'detailed.out'), 'r')
        lines = infile.readlines()
        infile.close()

        external_forces = []
        for n, line in enumerate(lines):
            if ('Forces on external charges' in line):
                chargestart = n + 1
                break
        else:
            raise RuntimeError(
                'Problem in reading forces on MM external-charges')
        lines1 = lines[chargestart:(chargestart + len(self.mmcharges))]
        for line in lines1:
            external_forces.append(
                [float(i) for i in line.split()])
        return np.array(external_forces) * Hartree / Bohr


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
