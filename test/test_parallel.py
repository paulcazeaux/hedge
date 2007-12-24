# Hedge - the Hybrid'n'Easy DG Environment
# Copyright (C) 2007 Andreas Kloeckner
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.




from __future__ import division
import unittest




class TestParallel(unittest.TestCase):
    def test_convergence_advec_3d(self):
        """Test whether 2D advection actually converges"""

        import pylinear.array as num
        import pylinear.computation as comp
        from hedge.mesh import make_ball_mesh, make_box_mesh
        from hedge.discretization import Discretization, pair_with_boundary
        from hedge.element import TetrahedralElement
        from hedge.timestep import RK4TimeStepper
        from hedge.tools import EOCRecorder
        from math import sin, pi, sqrt
        from hedge.operators import StrongAdvectionOperator
        from hedge.data import TimeDependentGivenFunction
        from hedge.parallel import guess_parallelization_context

        pcon = guess_parallelization_context()

        debug_output = False

        a = num.array([0.27,0])
        norm_a = comp.norm_2(a)

        def f(x):
            return sin(x)

        def u_analytic(x, t):
            return f((a*x/norm_a+t*norm_a))

        def boundary_tagger(vertices, el, face_nr):
            if el.face_normals[face_nr] * a > 0:
                return ["inflow"]
            else:
                return ["outflow"]

        for mesh in [
                # periodic
                make_box_mesh(dimensions=(2*pi, 2, 2), max_volume=0.4,
                    periodicity=(True, False, False),
                    boundary_tagger=boundary_tagger, 
                    ),
                # non-periodic
                make_ball_mesh(r=pi, boundary_tagger=boundary_tagger, max_volume=0.7),
                ]:
            for flux_type in StrongAdvectionOperator.flux_types:
                for random_partition in [True, False]:
                    eoc_rec = EOCRecorder()

                    if random_partition:
                        # Distribute elements randomly across nodes.
                        # This is bad, efficiency-wise, but it puts stress
                        # on the parallel implementation, which is desired here.
                        # Another main point of this is to force the code to split
                        # a periodic face pair across nodes.
                        from random import choice
                        partition = [choice(pcon.ranks) for el in mesh.elements]
                    else:
                        partition = None

                    for order in [1,2,3,4]:
                        if pcon.is_head_rank:
                            mesh_data = pcon.distribute_mesh(mesh, partition)
                        else:
                            mesh_data = pcon.receive_mesh()

                        discr = pcon.make_discretization(mesh_data, 
                                TetrahedralElement(order), debug=True)
                        op = StrongAdvectionOperator(discr, a, 
                                inflow_u=TimeDependentGivenFunction(u_analytic),
                                flux_type=flux_type)

                        u = discr.interpolate_volume_function(lambda x: u_analytic(x, 0))
                        dt = discr.dt_factor(norm_a)
                        nsteps = int(1/dt)
                        if debug_output and pcon.is_head_rank:
                            print "#steps=%d #elements=%d" % (nsteps, len(mesh.elements))

                        stepper = RK4TimeStepper()
                        for step in range(nsteps):
                            u = stepper(u, step*dt, dt, op.rhs)

                        u_true = discr.interpolate_volume_function(
                                lambda x: u_analytic(x, nsteps*dt))
                        error = u-u_true
                        my_l2_error = sqrt(error*(discr.mass_operator*error))

                        from boost.mpi import all_reduce
                        from operator import add
                        l2_error = all_reduce(pcon.communicator, my_l2_error, add)

                        eoc_rec.add_data_point(order, l2_error)

                    if debug_output and pcon.is_head_rank:
                        print "%s\n%s\n" % (flux_type.upper(), "-" * len(flux_type))
                        print eoc_rec.pretty_print(abscissa_label="Poly. Order", 
                                error_label="L2 Error")

                    self.assert_(eoc_rec.estimate_order_of_convergence()[0,1] > 3)
                    self.assert_(eoc_rec.estimate_order_of_convergence(2)[-1,1] > 7)





if __name__ == '__main__':
    unittest.main()