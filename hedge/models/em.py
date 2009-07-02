# -*- coding: utf8 -*-
"""Hedge operators modelling electromagnetic phenomena."""

from __future__ import division

__copyright__ = "Copyright (C) 2007 Andreas Kloeckner"

__license__ = """
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see U{http://www.gnu.org/licenses/}.
"""



from pytools import memoize_method

import hedge.mesh
from hedge.models import TimeDependentOperator




class MaxwellOperator(TimeDependentOperator):
    """A 3D Maxwell operator with PEC boundaries.

    Field order is [Ex Ey Ez Hx Hy Hz].
    """

    _default_dimensions = 3

    def __init__(self, epsilon, mu,
            flux_type,
            bdry_flux_type=None,
            pec_tag=hedge.mesh.TAG_ALL,
            absorb_tag=hedge.mesh.TAG_NONE,
            incident_tag=hedge.mesh.TAG_NONE,
            incident_bc=None, current=None, dimensions=None):
        """
        @arg flux_type: can be in [0,1] for anything between central and upwind,
          or "lf" for Lax-Friedrichs.
        """
        e_subset = self.get_eh_subset()[0:3]
        h_subset = self.get_eh_subset()[3:6]

        from hedge.tools import SubsettableCrossProduct
        self.e_cross = SubsettableCrossProduct(
                op2_subset=e_subset, result_subset=h_subset)
        self.h_cross = SubsettableCrossProduct(
                op2_subset=h_subset, result_subset=e_subset)

        from math import sqrt

        self.epsilon = epsilon
        self.mu = mu
        self.c = 1/sqrt(mu*epsilon)

        self.Z = sqrt(mu/epsilon)
        self.Y = 1/self.Z

        self.flux_type = flux_type
        if bdry_flux_type is None:
            self.bdry_flux_type = flux_type
        else:
            self.bdry_flux_type = bdry_flux_type

        self.pec_tag = pec_tag
        self.absorb_tag = absorb_tag
        self.incident_tag = incident_tag

        self.current = current
        self.incident_bc = incident_bc

        self.dimensions = dimensions or self._default_dimensions

    def flux(self, flux_type):
        from hedge.flux import make_normal, FluxVectorPlaceholder
        from hedge.tools import join_fields

        normal = make_normal(self.dimensions)

        from hedge.tools import count_subset
        w = FluxVectorPlaceholder(count_subset(self.get_eh_subset()))
        e, h = self.split_eh(w)

        if flux_type == "lf":
            return join_fields(
                    # flux e,
                    1/2*(
                        -1/self.epsilon*self.h_cross(normal, h.int-h.ext)
                        -self.c/2*(e.int-e.ext)
                    ),
                    # flux h
                    1/2*(
                        1/self.mu*self.e_cross(normal, e.int-e.ext)
                        -self.c/2*(h.int-h.ext))
                    )
        elif isinstance(flux_type, (int, float)):
            # see doc/maxima/maxwell.mac
            return join_fields(
                    # flux e,
                    1/self.epsilon*(
                        -1/2*self.h_cross(normal,
                            h.int-h.ext
                            -flux_type/self.Z*self.e_cross(normal, e.int-e.ext))
                        ),
                    # flux h
                    1/self.mu*(
                        1/2*self.e_cross(normal,
                            e.int-e.ext
                            +flux_type/(self.Y)*self.h_cross(normal, h.int-h.ext))
                        ),
                    )
        else:
            raise ValueError, "maxwell: invalid flux_type (%s)" % self.flux_type

    def local_op(self, e, h):
        def e_curl(field):
            return self.e_cross(nabla, field)

        def h_curl(field):
            return self.h_cross(nabla, field)

        from hedge.optemplate import make_nabla
        from hedge.tools import join_fields, count_subset

        nabla = make_nabla(self.dimensions)

        if self.current is not None:
            from hedge.optemplate import make_vector_field
            j = make_vector_field("j",
                    count_subset(self.get_eh_subset()[:3]))
        else:
            j = 0

        # in conservation form: u_t + A u_x = 0
        return join_fields(
                1/self.epsilon * (j - h_curl(h)),
                1/self.mu * e_curl(e),
                )

    def op_template(self, w=None):
        from hedge.optemplate import pair_with_boundary, \
                InverseMassOperator, get_flux_operator, \
                BoundarizeOperator

        from hedge.optemplate import make_vector_field

        from hedge.tools import count_subset
        fld_cnt = count_subset(self.get_eh_subset())
        if w is None:
            w = make_vector_field("w", fld_cnt)

        e, h = self.split_eh(w)

        # boundary conditions -------------------------------------------------
        from hedge.tools import join_fields

        # pec BC --------------------------------------------------------------
        pec_e = BoundarizeOperator(self.pec_tag) * e
        pec_h = BoundarizeOperator(self.pec_tag) * h
        pec_bc = join_fields(-pec_e, pec_h)

        # absorb BC -----------------------------------------------------------
        from hedge.optemplate import make_normal
        absorb_normal = make_normal(self.absorb_tag, self.dimensions)

        absorb_e = BoundarizeOperator(self.absorb_tag) * e
        absorb_h = BoundarizeOperator(self.absorb_tag) * h
        absorb_w = BoundarizeOperator(self.absorb_tag) * w

        absorb_bc = absorb_w + 1/2*join_fields(
                self.h_cross(absorb_normal, self.e_cross(absorb_normal, absorb_e))
                - self.Z*self.h_cross(absorb_normal, absorb_h),
                self.e_cross(absorb_normal, self.h_cross(absorb_normal, absorb_h)) 
                + self.Y*self.e_cross(absorb_normal, absorb_e)
                )

        if self.incident_bc is not None:
            from hedge.optemplate import make_common_subexpression
            incident_bc = make_common_subexpression(
                        -make_vector_field("incident_bc", fld_cnt))

        else:
            from hedge.tools import make_obj_array
            incident_bc = make_obj_array([0]*fld_cnt)

        # actual operator template --------------------------------------------
        m_inv = InverseMassOperator()

        flux_op = get_flux_operator(self.flux(self.flux_type))
        bdry_flux_op = get_flux_operator(self.flux(self.bdry_flux_type))

        return - self.local_op(e, h) \
                + m_inv*(
                    flux_op * w
                    +bdry_flux_op * pair_with_boundary(w, pec_bc, self.pec_tag)
                    +bdry_flux_op * pair_with_boundary(w, absorb_bc, self.absorb_tag)
                    +bdry_flux_op * pair_with_boundary(w, incident_bc, self.incident_tag))

    def bind(self, discr, **extra_context):
        from hedge.mesh import check_bc_coverage
        check_bc_coverage(discr.mesh, [
            self.pec_tag, self.absorb_tag, self.incident_tag])

        compiled_op_template = discr.compile(self.op_template())

        from hedge.tools import full_to_subset_indices
        e_indices = full_to_subset_indices(self.get_eh_subset()[0:3])
        all_indices = full_to_subset_indices(self.get_eh_subset())

        def rhs(t, w):
            if self.current is not None:
                j = self.current.volume_interpolant(t, discr)[e_indices]
            else:
                j = 0

            if self.incident_bc is not None:
                incident_bc = self.incident_bc.boundary_interpolant(
                        t, discr, self.incident_tag)[all_indices]
            else:
                incident_bc = 0

            return compiled_op_template(
                    w=w, j=j, incident_bc=incident_bc, **extra_context)

        return rhs

    def assemble_eh(self, e=None, h=None, discr=None):
        if discr is None:
            def zero(): return 0
        else:
            def zero(): return discr.volume_zeros()

        from hedge.tools import count_subset
        e_components = count_subset(self.get_eh_subset()[0:3])
        h_components = count_subset(self.get_eh_subset()[3:6])

        def default_fld(fld, comp):
            if fld is None:
                return [zero() for i in xrange(comp)]
            else:
                return fld

        e = default_fld(e, e_components)
        h = default_fld(h, h_components)

        from hedge.tools import join_fields
        return join_fields(e, h)

    @memoize_method
    def partial_to_eh_subsets(self):
        e_subset = self.get_eh_subset()[0:3]
        h_subset = self.get_eh_subset()[3:6]

        from hedge.tools import partial_to_all_subset_indices
        return tuple(partial_to_all_subset_indices(
            [e_subset, h_subset]))

    def split_eh(self, w):
        e_idx, h_idx = self.partial_to_eh_subsets()
        e, h = w[e_idx], w[h_idx]

        from hedge.flux import FluxVectorPlaceholder as FVP
        if isinstance(w, FVP):
            return FVP(scalars=e), FVP(scalars=h)
        else:
            from hedge.tools import make_obj_array as moa
            return moa(e), moa(h)

    def get_eh_subset(self):
        """Return a 6-tuple of C{bool}s indicating whether field components
        are to be computed. The fields are numbered in the order specified
        in the class documentation.
        """
        return 6*(True,)

    def max_eigenvalue(self):
        """Return the largest eigenvalue of Maxwell's equations as a hyperbolic system."""
        from math import sqrt
        return 1/sqrt(self.mu*self.epsilon)




class TMMaxwellOperator(MaxwellOperator):
    """A 2D TM Maxwell operator with PEC boundaries.

    Field order is [Ez Hx Hy].
    """

    _default_dimensions = 2

    def get_eh_subset(self):
        return (
                (False,False,True) # only ez
                +
                (True,True,False) # hx and hy
                )




class TEMaxwellOperator(MaxwellOperator):
    """A 2D TE Maxwell operator.

    Field order is [Ex Ey Hz].
    """

    _default_dimensions = 2

    def get_eh_subset(self):
        return (
                (True,True,False) # ex and ey
                +
                (False,False,True) # only hz
                )

class TEMaxwell1DOperator(MaxwellOperator):
    """A 1D TE Maxwell operator.

    Field order is [Ex Ey Hz].
    """

    _default_dimensions = 1

    def get_eh_subset(self):
        return (
                (True,True,False)
                +
                (False,False,True)
                )


class SourceFree1DMaxwellOperator(MaxwellOperator):
    """A 1D TE Maxwell operator.

    Field order is [Ey Hz].
    """

    _default_dimensions = 1

    def get_eh_subset(self):
        return (
                (False,True,False)
                +
                (False,False,True)
                )



