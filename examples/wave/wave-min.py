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

"""This is an example of the very minimum amount of code that's
necessary to get a hedge solver going."""




from __future__ import division
import numpy
import numpy.linalg as la




def main(write_output=True):
    from math import sin, exp, sqrt

    from hedge.mesh.generator import make_rect_mesh
    mesh = make_rect_mesh(a=(-0.5,-0.5),b=(0.5,0.5),max_area=0.008)

    from hedge.backends.jit import Discretization

    discr = Discretization(mesh, order=4)

    from hedge.visualization import VtkVisualizer
    vis = VtkVisualizer(discr, None, "fld")

    def source_u(x, el):
        x = x - numpy.array([0.1,0.22])
        return exp(-numpy.dot(x, x)*128)

    from hedge.data import \
            make_tdep_given, \
            TimeHarmonicGivenFunction, \
            TimeIntervalGivenFunction

    from hedge.models.wave import StrongWaveOperator
    from hedge.mesh import TAG_ALL, TAG_NONE
    op = StrongWaveOperator(-1, discr.dimensions, 
            source_f=TimeIntervalGivenFunction(
                TimeHarmonicGivenFunction(
                    make_tdep_given(source_u), omega=10),
                0, 1),
            dirichlet_tag=TAG_NONE,
            neumann_tag=TAG_NONE,
            radiation_tag=TAG_ALL,
            flux_type="upwind")

    from hedge.tools import join_fields
    fields = join_fields(discr.volume_zeros(),
            [discr.volume_zeros() for i in range(discr.dimensions)])

    from hedge.timestep import RK4TimeStepper
    stepper = RK4TimeStepper()
    dt = op.estimate_timestep(discr, stepper=stepper, fields=fields)

    nsteps = int(1/dt)
    print "dt=%g nsteps=%d" % (dt, nsteps)

    rhs = op.bind(discr)
    for step in range(nsteps):
        t = step*dt

        if step % 50 == 0 and write_output:
            print step, t, discr.norm(fields[0])
            visf = vis.make_file("fld-%04d" % step)

            vis.add_data(visf,
                    [ ("u", fields[0]), ("v", fields[1:]), ],
                    time=t, step=step)
            visf.close()

        fields = stepper(fields, t, dt, rhs)

    vis.close()




if __name__ == "__main__":
    main()
