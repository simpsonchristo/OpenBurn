from copy import deepcopy
from math import sqrt
from numpy import mean

# from qtpy.QtCore import QObject, Signal, Slot

from openburn.core.motor import OpenBurnMotor
from openburn.core.grain import OpenBurnGrain

from openburn.util import Q_

MAX_SIM_TIME = 1000     # maximum simulation time in seconds before failing sim


class SimSettings:
    """Params that control how the InternalBallisticsSim runs"""
    def __init__(self, pres: float, temp: float, twophase: float, skinfric: float, timestep: float = 0.01):
        self.ambient_pressure: float = pres
        self.ambient_temp: float = temp
        self.two_phase_flow_eff: float = twophase   # % of combustion that is gaseous. BurnSim assumes 85% by default.
        self.skin_friction_eff: float = skinfric    # 98-99% is typical for this value
        self.time_step: float = timestep


class SimDataPoint:
    """Simulation data at a given discrete time step"""
    def __init__(self):
        self.motor: OpenBurnMotor = None
        self.pressure: float = 0
        self.mass_flux: float = 0
        self.thrust: float = 0
        self.isp: float = 0
        self.kn: float = 0

        self.burn_rate: float = 0
        self.time_stamp: float = 0


class SimResults:
    """The results of running an InternalBallisticsSim"""
    def __init__(self, data_points, burn_time, total_impulse):
        self.data = data_points
        self.burn_time = burn_time
        self.total_impulse = total_impulse

    def get_max_presure(self):
        return max(x.pressure for x in self.data)

    def get_max_thrust(self):
        return max(x.thrust for x in self.data)

    def get_avg_thrust(self):
        return mean(x.thrust for x in self.data)

    def get_max_mass_flux(self):
        return max(x.mass_flux for x in self.data)

    def get_avg_isp(self):
        return mean(x.thrust for x in self.data)

    def get_kn_range(self):
        """Returns a tuple of the kn range (min, max)"""
        min_ = min(x.kn for x in self.data)
        max_ = max(x.kn for x in self.data)
        return min_, max_


class SimulationException(Exception):
    """Exception raised when the simulation encounters an error"""
    def __init__(self, message):
        super(SimulationException, self).__init__(message)


class InternalBallisticsSim:
    """The internal ballistics simulator
    Calculates internal ballistics params"""

    def __init__(self):
        super(InternalBallisticsSim, self).__init__()

    @classmethod
    def run_sim(cls, motor: OpenBurnMotor, settings: SimSettings) -> "SimResults":
        """
        Regression simulation
        Calculates internal ballistics regression and info
        :param motor: the initial motor, regressing at discrete time steps, with settings controlled by
        :param settings:
        :returns SimResults: an object that encapsulates the results of the simulation run"""

        iterations: int = 0
        num_grains_burned: int = 0
        total_burn_time: float = 0
        total_impulse: float = 0

        data = []

        prev_data = None

        while num_grains_burned < motor.get_num_grains():
            current_data = SimDataPoint()

            # clone motor data for initial condition
            if iterations == 0:
                current_data.motor = deepcopy(motor)
            else:
                current_data.motor = deepcopy(prev_data.motor)

            current_motor = current_data.motor

            # regression simulation for each grain
            for grain in current_motor.grains:
                if not grain.burned_out():
                    burnrate = cls.calc_steady_state_burn_rate(current_motor, grain)
                    current_data.burn_rate = burnrate

                    if not grain.burn(burnrate, settings.time_step):
                        num_grains_burned += 1

            # set simulation data for this time step after regression
            current_data.pressure = cls.calc_chamber_pressure(current_motor)
            current_data.time = total_burn_time
            current_data.thrust = cls.calc_thrust(current_motor, settings)
            current_data.mass_flux = cls.calc_mass_flux(current_motor, current_motor.length())
            current_data.isp = cls.calc_isp(current_motor, settings)
            current_data.kn = current_motor.get_kn()

            # add data to results
            data.append(current_data)

            # update motor info
            total_impulse += current_data.thrust * settings.time_step
            total_burn_time += settings.time_step

            # set up for next iteration
            iterations += 1
            prev_data = current_data

            # fallback failure state: MAX_SIM_TIME second burn time
            if total_burn_time > MAX_SIM_TIME:
                raise SimulationException(f"Error! Simulation exceeded {MAX_SIM_TIME} seconds.")

        results = SimResults(data_points=data, total_impulse=total_impulse, burn_time=total_burn_time)
        return results

    @classmethod
    def calc_thrust(cls, motor: OpenBurnMotor, settings: SimSettings) -> float:
        """
        Calculates the actual thrust using empirical inefficiency factors,
        then applys that Cf to the throat area and pressure to give the real thrust value
        :param motor: the motor to calculate thrust for
        :param settings: inefficiency fudge factors
        :return: thrust in lbs

        http://www.dtic.mil/dtic/tr/fulltext/u2/a099791.pdf
        Cf_real = Nd * Nt * (Nf * Cf_v + (1 - nf))
        where:
            Nd is divergence loss %
            Nt is two phase flow loss %
            Nf is skin friction loss %
            Cv_f is ideal thrust coeff.
        """
        nozzle = motor.nozzle

        Pc = cls.calc_chamber_pressure(motor)
        Cf_v = cls.calc_ideal_thrust_coeff(motor, settings, Pc)

        Nd = nozzle.get_divergence_loss()
        Nt = settings.two_phase_flow_eff
        Nf = settings.skin_friction_eff

        Cf_real = Nd * Nt * (Nf * Cf_v + (1 - Nf))

        # thrust = Cf * At * Pc
        return Cf_real * nozzle.get_throat_area() * Pc

    @classmethod
    def calc_ideal_thrust_coeff(cls, motor: OpenBurnMotor, settings: SimSettings, chamber_pressure: float) -> float:
        """
        Calculates the ideal thrust coefficient at a given chamber pressure using
        the isentropic flow equations
        :param motor:
        :param settings:
        :param chamber_pressure:
        :return: Cf_v, the ideal thrust coefficient

        Cf = sqrt [ (2k^2 / k-1) (2/k+1 )^k+1k-1 * 1- (P2/P1)^(k-1/k) )] + (p2-p3)A2 / Pc
        See Rocket Propulsion Elements, Eq. 3-29
        """
        # simplify terms involving k (gamma)
        k = motor.get_gamma()
        k_square = 2 * k * k / (k - 1)
        two_over_k = 2 / (k + 1)
        k_over_k = (k + 1) / (k - 1)
        k_minus_1 = (k - 1) / k

        # set up other variables
        exp_ratio = motor.nozzle.get_expansion_ratio()
        exit_pressure = cls.calc_exit_pressure(motor, chamber_pressure)
        pressure_ratio = exit_pressure / chamber_pressure

        momentum_thrust = sqrt(k_square * two_over_k ** k_over_k) * (1 - pressure_ratio ** k_minus_1)
        pressure_thrust = ((exit_pressure - settings.ambient_pressure * exp_ratio) / chamber_pressure)
        return momentum_thrust + pressure_thrust

    @classmethod
    def calc_exit_pressure(cls, motor: OpenBurnMotor, chamber_pressure: float) -> float:
        """
        Calculates nozzle exit pressure using the exit mach number
        :param motor: the motor
        :param chamber_pressure: current chamber pressure
        :return: nozzle exit pressure, in lbs/in^3
        """
        gamma = motor.get_gamma()

        # find exit mach number
        exit_mach = cls.calc_exit_mach(motor)

        # calculate the ratio of pressures Pc/Pe
        pressure_ratio = (1 + 0.5*(gamma - 1) * exit_mach**2) ** -(gamma / (gamma - 1))
        # multiply by Pc to get Pe alone
        return chamber_pressure * pressure_ratio

    @classmethod
    def calc_exit_mach(cls, motor: OpenBurnMotor) -> float:
        """
        Calculates the exit mach number from the nozzle area ratio and gamma.
        Numerically solves the isentropic flow equation for exit mach number
        :param motor: the motor
        :return: the exit mach number
        see https://www.grc.nasa.gov/www/k-12/airplane/rktthsum.html
        """
        if motor.nozzle.exit_dia <= motor.nozzle.throat_dia:
            return 1.0

        mach_number = 2.2   # init with some arbitrary supersonic mach number
        gamma = motor.get_gamma()
        area_ratio = motor.nozzle.get_expansion_ratio()
        gp1 = gamma + 1
        gm1 = gamma - 1
        exponent = gp1 / (2 * gm1)

        # initial guesses
        guess_area_ratio = area_ratio / 2
        guess_mach = mach_number

        # newton's method for numerical approximation
        while abs(area_ratio - guess_area_ratio) > 0.0001:
            # find area ratio using our guesses
            base = 1 + 0.5 * gm1 * guess_mach**2
            new_area_ratio = 1 / (guess_mach * base ** -exponent) * (gp1 / 2) ** exponent

            # find change (derivative)
            deriv = (new_area_ratio - guess_area_ratio) / (guess_mach - mach_number)

            # update guesses
            guess_area_ratio = new_area_ratio
            mach_number = guess_mach

            # update mach
            guess_mach = mach_number + (area_ratio - guess_area_ratio) / deriv

        return mach_number

    @classmethod
    def calc_chamber_pressure(cls, motor: OpenBurnMotor) -> float:
        """
        Calculates chamber pressure assuming a steady-state chamber
        p = (Kn * a * rho * C* )^(1/(1-n))
        see Rocket Propulsion Elements, Eq. ??

        :param motor:
        :return: steady-state chamber pressure, in lbs/in^3
        """
        rho = Q_(motor.get_propellant_density(), 'lb/in3')
        rho = rho.to('slug/in3').magnitude
        cstar = motor.get_cstar()
        exp = 1 / (1 - motor.get_ballistic_n())
        base = motor.get_kn() * motor.get_ballistic_a() * rho * cstar
        return base ** exp

    @classmethod
    def calc_steady_state_burn_rate(cls, motor: OpenBurnMotor, grain: OpenBurnGrain) -> float:
        """
        Calculates the steady-state burn rate for a given grain

        :param motor:
        :param grain:
        :return: steady state burn rate (R_0) in inches/second
        """
        prop = grain.propellant
        Pc = cls.calc_chamber_pressure(motor)
        return prop.a * Pc ** prop.n

    @classmethod
    def calc_isp(cls, motor: OpenBurnMotor, settings: SimSettings) -> float:
        """
        Calculates Isp given a motor
        Isp = F / mdot * g
        :param settings:
        :param motor:
        :return: Isp in seconds
        """
        return cls.calc_thrust(motor, settings) / motor.get_mass_flow()

    @classmethod
    def calc_mass_flux(cls, motor: OpenBurnMotor, x_val: float) -> float:
        """
        Calculates the mass flux at the given x coordinate
        :param x_val: x value of the point to find mass flux,
            where x = 0 is at the head end and x = len is the end of the propellant surface.

        :param motor:
        :return: Mass flux in lbs/sec/in^2
        """
        grain = motor.get_grain_at_x(x_val)
        if grain:
            return motor.get_upstream_mass_flow(x_val) / grain.get_port_area()

        raise Exception(f"Grain not found at x value: {x_val}")