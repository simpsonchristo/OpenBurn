import unittest

from openburn.core.internalballistics import SimSettings, InternalBallisticsSim as sim
from openburn.core.propellant import SimplePropellant
from openburn.core.grain import CylindricalCoreGrain
from openburn.core.nozzle import ConicalNozzle
from openburn.core.motor import OpenBurnMotor

from openburn.util import Q_


class InternalBallisticsTest(unittest.TestCase):
    def setUp(self):
        """Set up the test data"""
        self.settings = SimSettings(twophase=0.85, timestep=0.01)
        self.propellant = SimplePropellant("Test Propellant", 0.015, 0.4, 5000, 0.06)
        # using a list comprehension to create four unique grain objects
        self.grains = [CylindricalCoreGrain(diameter=2, length=4, core_diameter=1, burning_faces=2,
                                            propellant=self.propellant)
                       for _ in range(0, 4)]
        self.nozzle = ConicalNozzle(throat=0.5, exit=2, half_angle=15, throat_len=0.25)
        self.motor = OpenBurnMotor()
        self.motor.set_grains(self.grains)
        self.motor.set_nozzle(self.nozzle)

    def test_basic_sim(self):
        results = sim.run_sim(self.motor, self.settings)
        print("\nResults:")
        print("Kn Range: ", results.get_kn_range())
        print("Burn Time: (s)", results.get_burn_time())
        print("Max Pressure: (psi)", results.get_max_presure())
        print("Average Isp: (s)", results.get_avg_isp())
        print("Max Isp: (s)", results.get_max_isp())
        print("Max Mass flux: (lb/sec/in^2)", results.get_max_mass_flux())

        avg_thrust_lb = results.get_avg_thrust()
        avg_thrust_n = Q_(avg_thrust_lb, 'lbf').to('newton').magnitude
        print("Avg Thrust: (lbs)", avg_thrust_lb)
        print("Avg Thrust: (newtons)", avg_thrust_n)

        impulse_lb = results.get_total_impulse()
        impulse_n = Q_(impulse_lb, 'lbf').to('newton').magnitude
        print("Total Impulse (lb-sec)", impulse_lb)
        print("Total Impulse (newton-sec)", impulse_n)
