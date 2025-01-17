import os

import numpy as np
import pandas as pd

from .biomech_system import BiomechCoordinateSystem
from .checks import (
    check_segment_filled_with_nan,
    check_is_isb_segment,
    check_is_euler_sequence_provided,
    check_is_translation_provided,
    check_parent_child_joint,
    check_is_isb_correctable,
    check_correction_methods,
)
from .corrections.angle_conversion_callbacks import (
    isb_framed_rotation_matrix_from_euler_angles,
    set_corrections_on_rotation_matrix,
    rotation_matrix_2_euler_angles,
    to_left_handed_frame,
)
from .corrections.kolz_matrices import get_kolz_rotation_matrix
from .deviation import Deviation
from .enums import (
    Segment,
    Frame,
    Correction,
    DataFolder,
    EulerSequence,
    BiomechDirection,
    BiomechOrigin,
    JointType,
)
from .joint import Joint
from .load_data import load_euler_csv
from .utils import (
    get_segment_columns,
    get_correction_column,
    get_is_correctable_column,
    get_is_isb_column,
)


class RowData:
    """
    This class is used to store the data of a row of the dataset and make it accessible through attributes and methods.
    """

    def __init__(self, row: pd.Series):
        """
        Parameters
        ----------
        row : pandas.Series
            The row of the dataset to store.
        """
        self.row = row

        self.parent_segment = Segment.from_string(self.row.parent)
        self.parent_columns = get_segment_columns(self.parent_segment)

        self.child_segment = Segment.from_string(self.row.child)
        self.child_columns = get_segment_columns(self.child_segment)

        self.joint = None
        self.right_side = True

        self.parent_biomech_sys = None
        self.parent_corrections = None

        self.child_biomech_sys = None
        self.child_corrections = None

        self.has_rotation_data = None
        self.has_translation_data = None

        self.parent_segment_usable_for_rotation_data = None
        self.child_segment_usable_for_rotation_data = None

        self.parent_segment_usable_for_translation_data = None
        self.child_segment_usable_for_translation_data = None

        self.parent_definition_risk = None
        self.child_definition_risk = None

        self.usable_rotation_data = None
        self.usable_translation_data = None

        self.rotation_data_risk = None
        self.translation_data_risk = None

        self.euler_angles_correction_callback = None
        self.translation_correction_callback = None
        self.translation_isb_matrix_callback = None

        self.csv_filenames = None
        self.data = None
        self.corrected_data = None
        self.melted_corrected_data = None

    @property
    def left_side(self):
        return not self.right_side

    def check_all_segments_validity(self, print_warnings: bool = False) -> bool:
        """
        Check all the segments of the row are valid.
        First, we check if the segment is provided, i.e., no NaN values.
        Second, we check if the segment defined as is_isb = True or False in the dataset
        and if the orientations of axis defined in the dataset fits with isb definition.

        (we don't mind if it's not a isb segment, we just don't want to have a segment
        that matches the is_isb given)

        Third, we check the frame are direct, det(R) = 1. We want to have a direct frame.

        Returns
        -------
        bool
            True if all the segments are valid, False otherwise.
        """
        output = True
        for segment_enum in Segment:
            segment_cols = get_segment_columns(segment_enum)
            # first check
            if check_segment_filled_with_nan(self.row, segment_cols, print_warnings=print_warnings):
                continue

            # build the coordinate system
            bsys = BiomechCoordinateSystem.from_biomech_directions(
                x=BiomechDirection.from_string(self.row[segment_cols[0]]),
                y=BiomechDirection.from_string(self.row[segment_cols[1]]),
                z=BiomechDirection.from_string(self.row[segment_cols[2]]),
                origin=BiomechOrigin.from_string(self.row[segment_cols[3]]),
                segment=segment_enum,
            )
            # second check
            if not check_is_isb_segment(self.row, bsys, print_warnings=print_warnings):
                output = False

            if not check_is_isb_correctable(self.row, bsys, print_warnings=print_warnings):
                output = False

            if not check_correction_methods(self, bsys, print_warnings=print_warnings):
                output = False

            # third check if the segment is direct or not
            if not bsys.is_direct():
                if print_warnings:
                    print(
                        f"{self.row.dataset_authors}, "
                        f"Segment {segment_enum.value} is not direct, "
                        f"it should be !!!"
                    )
                output = False

        return output

    def check_joint_validity(self, print_warnings: bool = False) -> bool:
        """
        Check if the joint defined in the dataset is valid.
        We expect the joint to have a valid euler sequence, i.e., no NaN values., three letters and valid letters.
        If not we expect the joint to have a valid translation, i.e., no NaN values.

        We expect the joint to have good parent and child definitions
        We expect the joint to have defined parent and child segments, i.e., no NaN values.

        Returns
        -------
        bool
            True if the joint is valid, False otherwise.
        """
        output = True

        # todo: separate as much as possible the rotations checks and the translations checks

        no_euler_sequence = not check_is_euler_sequence_provided(self.row, print_warnings=print_warnings)
        no_translation = not check_is_translation_provided(self.row, print_warnings=print_warnings)

        self.has_rotation_data = not no_euler_sequence
        self.has_translation_data = not no_translation

        if no_euler_sequence and no_translation:
            output = False
            if print_warnings:
                print(
                    f"Joint {self.row.joint} has no euler sequence defined, "
                    f" and no translation defined, "
                    f"it should not be empty !!!"
                )
            return output

        if no_euler_sequence:  # Only translation is provided
            self.joint = Joint(
                joint_type=JointType.from_string(self.row.joint),
                euler_sequence=EulerSequence.from_string(self.row.euler_sequence),  # throw a None
                translation_origin=BiomechOrigin.from_string(self.row.origin_displacement),
                translation_frame=Frame.from_string(self.row.displacement_cs, self.row.joint),
            )

        elif no_translation:  # Only rotation is provided
            self.joint = Joint(
                joint_type=JointType.from_string(self.row.joint),
                euler_sequence=EulerSequence.from_string(self.row.euler_sequence),
                translation_origin=None,
                translation_frame=None,
            )

        else:  # translation and rotation are both provided
            self.joint = Joint(
                joint_type=JointType.from_string(self.row.joint),
                euler_sequence=EulerSequence.from_string(self.row.euler_sequence),
                translation_origin=BiomechOrigin.from_string(self.row.origin_displacement),
                translation_frame=Frame.from_string(self.row.displacement_cs, self.row.joint),
            )

        if not check_parent_child_joint(self.joint, row=self.row, print_warnings=print_warnings):
            output = False

        # check database if nan in one the segment of the joint
        if check_segment_filled_with_nan(self.row, self.parent_columns, print_warnings=print_warnings):
            output = False
            if print_warnings:
                print(
                    f"Joint {self.row.joint} has a NaN value in the parent segment {self.row.parent}, "
                    f"it should not be empty !!!"
                )

        if check_segment_filled_with_nan(self.row, self.child_columns, print_warnings=print_warnings):
            output = False
            if print_warnings:
                print(
                    f"Joint {self.row.joint} has a NaN value in the child segment {self.row.child}, "
                    f"it should not be empty !!!"
                )

        return output

    def set_segments(self):
        """
        Set the parent and child segments of the joint.
        """

        self.parent_biomech_sys = BiomechCoordinateSystem.from_biomech_directions(
            x=BiomechDirection.from_string(self.row[self.parent_columns[0]]),
            y=BiomechDirection.from_string(self.row[self.parent_columns[1]]),
            z=BiomechDirection.from_string(self.row[self.parent_columns[2]]),
            origin=BiomechOrigin.from_string(self.row[self.parent_columns[3]]),
            segment=self.parent_segment,
        )
        self.child_biomech_sys = BiomechCoordinateSystem.from_biomech_directions(
            x=BiomechDirection.from_string(self.row[self.child_columns[0]]),
            y=BiomechDirection.from_string(self.row[self.child_columns[1]]),
            z=BiomechDirection.from_string(self.row[self.child_columns[2]]),
            origin=BiomechOrigin.from_string(self.row[self.child_columns[3]]),
            segment=self.child_segment,
        )

    def extract_corrections(self, segment: Segment) -> str:
        """
        Extract the correction cell of the correction column.
        ex: if the correction column is parent_to_isb, we extract the correction cell parent_to_isb
        """
        correction_column = get_correction_column(segment)
        correction_cell = self.row[correction_column]

        if correction_cell == "nan":
            correction_cell = None
        if not isinstance(correction_cell, str) and correction_cell is not None:
            if np.isnan(correction_cell):
                correction_cell = None

        if correction_cell is not None:
            # separate strings with a comma in several element of list
            correction_cell = correction_cell.replace(" ", "").split(",")
            for i, correction in enumerate(correction_cell):
                correction_cell[i] = Correction.from_string(correction)

        return correction_cell

    def extract_is_thorax_global(self, segment: Segment) -> bool:
        if segment != Segment.THORAX:
            raise ValueError("The segment is not the thorax")
        else:
            return self.row["thorax_is_global"]

    def extract_is_correctable(self, segment: Segment) -> bool:
        """
        Extract the database entry to state if the segment is correctable or not.
        """

        if self.row[get_is_correctable_column(segment)] is not None and np.isnan(
            self.row[get_is_correctable_column(segment)]
        ):
            return None
        if self.row[get_is_correctable_column(segment)] == "nan":
            return None
        if self.row[get_is_correctable_column(segment)] == "true":
            return True
        if self.row[get_is_correctable_column(segment)] == "false":
            return False
        if self.row[get_is_correctable_column(segment)]:
            return True
        if not self.row[get_is_correctable_column(segment)]:
            return False

        raise ValueError("The is_correctable column is not a boolean value")

    def extract_is_isb(self, segment: Segment) -> bool:
        """Extract the database entry to state if the segment is isb or not."""
        if self.row[get_is_isb_column(segment)] is not None and np.isnan(self.row[get_is_isb_column(segment)]):
            return None
        if self.row[get_is_isb_column(segment)] == "nan":
            return None
        if self.row[get_is_isb_column(segment)] == "true":
            return True
        if self.row[get_is_isb_column(segment)] == "false":
            return False
        if self.row[get_is_isb_column(segment)]:
            return True
        if not self.row[get_is_isb_column(segment)]:
            return False

        raise ValueError("The is_isb column is not a boolean value")

    def _check_segment_has_no_correction(self, correction, print_warnings: bool = False) -> bool:
        if correction is not None:
            output = False
            if print_warnings:
                print(
                    f"Joint {self.row.joint} has a correction value in the child segment {self.row.parent}, "
                    f"it should be empty !!!, because the segment is isb. "
                    f"Parent correction: {correction}"
                )
        else:
            output = True
        return output

    def _check_segment_has_kolz_correction(self, correction, print_warnings: bool = False) -> bool:
        correction = [] if correction is None else correction
        condition_scapula = (
            Correction.SCAPULA_KOLZ_AC_TO_PA_ROTATION in correction
            or Correction.SCAPULA_KOLZ_GLENOID_TO_PA_ROTATION in correction
        )
        if not condition_scapula:
            output = False
            if print_warnings:
                print(
                    f"Joint {self.row.joint} has no correction value in the segment Scapula, "
                    f"it should be filled with a {Correction.SCAPULA_KOLZ_AC_TO_PA_ROTATION} or a "
                    f"{Correction.SCAPULA_KOLZ_GLENOID_TO_PA_ROTATION} correction, because the segment "
                    f"origin is not on an isb axis. "
                    f"Current value: {correction}"
                )
        else:
            output = True
        return output

    def _check_segment_has_to_isb_correction(self, correction, print_warnings: bool = False) -> bool:
        correction = [] if correction is None else correction
        if not (Correction.TO_ISB_ROTATION in correction):
            output = False
            if print_warnings:
                print(
                    f"Joint {self.row.joint} has no correction value in the parent segment {self.row.parent}, "
                    f"it should be filled with a {Correction.TO_ISB_ROTATION}, because the segment is not isb. "
                    f"Current value: {correction}"
                )
        else:
            output = True
        return output

    def _check_segment_has_to_isb_like_correction(self, correction, print_warnings: bool = False) -> bool:
        correction = [] if correction is None else correction
        if not (Correction.TO_ISB_LIKE_ROTATION in correction):
            output = False
            if print_warnings:
                print(
                    f"Joint {self.row.joint} has no correction value in the parent segment {self.row.parent}, "
                    f"it should be filled with a "
                    f"{Correction.TO_ISB_LIKE_ROTATION} correction, because the segment is not isb. "
                    f"Current value: {correction}"
                )
        else:
            output = True
        return output

    def _check_segment_has_to_isb_or_like_correction(self, correction, print_warnings: bool = False) -> bool:
        correction = [] if correction is None else correction
        output = self._check_segment_has_to_isb_like_correction(correction, print_warnings=False)
        if not output:
            output = self._check_segment_has_to_isb_correction(correction, print_warnings=False)
        if not output:
            if print_warnings:
                print(
                    f"Joint {self.row.joint} has no correction value in the parent segment {self.row.parent}, "
                    f"it should be filled with a "
                    f"{Correction.TO_ISB_LIKE_ROTATION} or {Correction.TO_ISB_ROTATION} "
                    f"correction, because the segment is not isb. "
                    f"Current value: {correction}"
                )
        return output

    def check_segments_correction_validity(self, print_warnings: bool = False) -> tuple[bool, bool]:
        """
        We expect the correction columns to be filled with valid values.
        ex: if both segment are not isb, we expect the correction to_isb to be filled
        ex: if both segment are isb, we expect no correction to be filled
        ex: if both segment are isb, and euler sequence is isb, we expect no correction to be filled
        ex: if both segment are isb, and euler sequence is not isb, we expect the correction to_isb to be filled
        etc...

        Return
        ------
        output:tuple[bool, bool]
            rotation_data_validity, translation_data_validity
        """
        parent_output = True
        child_output = True

        parent_correction = self.extract_corrections(self.parent_segment)
        self.parent_corrections = self.extract_corrections(self.parent_segment)
        parent_is_correctable = self.extract_is_correctable(self.parent_segment)
        parent_is_thorax_global = False

        child_correction = self.extract_corrections(self.child_segment)
        self.child_corrections = self.extract_corrections(self.child_segment)
        # child_is_correctable = self.extract_is_correctable(self.child_segment)

        # Thorax is global check
        if self.parent_segment == Segment.THORAX:
            if self.extract_is_thorax_global(self.parent_segment):
                parent_is_thorax_global = True
                if parent_is_correctable is True:
                    parent_output = self._check_segment_has_to_isb_like_correction(
                        parent_correction, print_warnings=print_warnings
                    )
                elif parent_is_correctable is False:
                    parent_output = self._check_segment_has_no_correction(
                        parent_correction, print_warnings=print_warnings
                    )
                else:
                    print(
                        "The correction of thorax should be filled with a boolean value, "
                        "as it is a global coordinate system."
                    )

                self.parent_segment_usable_for_rotation_data = parent_output
                self.parent_segment_usable_for_translation_data = False
                self.parent_definition_risk = True
            else:
                parent_is_thorax_global = False

        # if both segments are isb oriented, but origin is on an isb axis, we expect no correction be filled
        # so that we can consider rotation data as isb
        if (
            self.parent_biomech_sys.is_isb_oriented()
            and self.parent_biomech_sys.is_origin_on_an_isb_axis()
            and not parent_is_thorax_global
        ):
            parent_output = self._check_segment_has_no_correction(parent_correction, print_warnings=print_warnings)
            self.parent_segment_usable_for_rotation_data = parent_output
            self.parent_segment_usable_for_translation_data = False

        if self.child_biomech_sys.is_isb_oriented() and self.child_biomech_sys.is_origin_on_an_isb_axis():
            child_output = self._check_segment_has_no_correction(child_correction, print_warnings=print_warnings)
            self.child_segment_usable_for_rotation_data = child_output
            self.child_segment_usable_for_translation_data = False

        if (
            self.parent_biomech_sys.is_isb_oriented()
            and not self.parent_biomech_sys.is_origin_on_an_isb_axis()
            and not parent_is_thorax_global
        ):
            # if self.parent_segment == Segment.SCAPULA:
            # parent_output = self._check_segment_has_kolz_correction(
            #     parent_correction, print_warnings=print_warnings
            # )
            # else:
            # self.parent_definition_risk = True
            self.parent_segment_usable_for_rotation_data = True
            self.parent_segment_usable_for_translation_data = False

        if self.child_biomech_sys.is_isb_oriented() and not self.child_biomech_sys.is_origin_on_an_isb_axis():
            child_output = True
            if self.child_segment == Segment.SCAPULA:
                child_output = True
                # parent_output = self._check_segment_has_kolz_correction(child_correction, print_warnings=print_warnings)
            else:
                self.child_definition_risk = True
            self.child_segment_usable_for_rotation_data = child_output
            self.child_segment_usable_for_translation_data = False

        # if segments are not isb, we expect the correction to_isb to be filled
        if (
            not self.parent_biomech_sys.is_isb_oriented()
            and self.parent_biomech_sys.is_origin_on_an_isb_axis()
            and not parent_is_thorax_global
        ):
            parent_output = True
            # parent_output = self._check_segment_has_to_isb_or_like_correction(
            #     parent_correction, print_warnings=print_warnings
            # )
            # if self.parent_segment == Segment.SCAPULA:
            #     parent_output = self._check_segment_has_kolz_correction(parent_correction, print_warnings=print_warnings)
            # I believe there should be a kolz correction when the origin is on an isb axis
            # if self.parent_segment == Segment.SCAPULA:
            # if self._check_segment_has_kolz_correction(parent_correction, print_warnings=False):
            #     parent_output = False
            #     print("WARNING: Kolz correction should not be filled when the origin is on an isb axis")
            self.parent_segment_usable_for_rotation_data = parent_output
            self.parent_segment_usable_for_translation_data = False

        if not self.child_biomech_sys.is_isb_oriented() and self.child_biomech_sys.is_origin_on_an_isb_axis():
            # child_output = self._check_segment_has_to_isb_or_like_correction(
            #     child_correction, print_warnings=print_warnings
            # )
            child_output = True
            # if self.child_segment == Segment.SCAPULA:
            #     child_output = self._check_segment_has_kolz_correction(child_correction, print_warnings=print_warnings)
            # I believe there should be a kolz correction when the origin is on an isb axis
            # if self.child_segment == Segment.SCAPULA:
            #     if self._check_segment_has_kolz_correction(child_correction, print_warnings=False):
            #         child_output = False
            #         print("WARNING: Kolz correction should not be filled when the origin is on an isb axis")
            self.child_segment_usable_for_rotation_data = child_output
            self.child_segment_usable_for_translation_data = False

        if (
            not self.parent_biomech_sys.is_isb_oriented()
            and not self.parent_biomech_sys.is_origin_on_an_isb_axis()
            and not parent_is_thorax_global
        ):
            parent_output = True
            if self.parent_segment == Segment.SCAPULA:
                # parent_output = self._check_segment_has_kolz_correction(
                #     parent_correction, print_warnings=print_warnings
                # )
                self.parent_segment_usable_for_rotation_data = parent_output
                self.parent_segment_usable_for_translation_data = False
                self.parent_definition_risk = True  # should be a less high risk. because known from the literature
            else:
                parent_output = True
                # parent_output = self._check_segment_has_to_isb_like_correction(
                #     parent_correction, print_warnings=print_warnings
                # )
                #
                # if not parent_is_correctable:
                #     parent_output = self._check_segment_has_no_correction(
                #         parent_correction, print_warnings=print_warnings
                #     )
                # else:
                #     print(
                #         f"The column is_correctable should not be filled with a True for {self.parent_segment} segment."
                #     )

                self.parent_segment_usable_for_rotation_data = parent_output
                self.parent_segment_usable_for_translation_data = False
                self.parent_definition_risk = True

            # todo: please implement the following risks
            # self.parent_definition_risk = Risk.LOW  # known and corrected from the literature
            # self.parent_definition_risk = Risk.HIGH  # unknown and uncorrected from the literature

        if not self.child_biomech_sys.is_isb_oriented() and not self.child_biomech_sys.is_origin_on_an_isb_axis():
            child_output = True
            if self.child_segment == Segment.SCAPULA:
                # child_output = (self._check_segment_has_to_isb_correction(
                #     child_correction, print_warnings=print_warnings
                # ) and
                # child_output = self._check_segment_has_kolz_correction(child_correction, print_warnings=print_warnings)
                self.child_segment_usable_for_rotation_data = child_output
                self.child_segment_usable_for_translation_data = False
                self.child_definition_risk = True  # should be a less high risk. because known from the literature
            else:
                child_output = True
                # child_output = self._check_segment_has_to_isb_like_correction(
                #     child_correction, print_warnings=print_warnings
                # )
                #
                # if not parent_is_correctable:
                #     child_output = self._check_segment_has_no_correction(
                #         child_correction, print_warnings=print_warnings
                #     )
                # else:
                #     print(
                #         f"The column is_correctable should not be filled with a True for {self.child_segment} segment."
                #     )

                self.parent_segment_usable_for_rotation_data = child_output
                self.parent_segment_usable_for_translation_data = False
                self.parent_definition_risk = True

        # finally check the combination of parent and child to determine if usable for rotation and translation
        self.usable_rotation_data = (
            self.child_segment_usable_for_rotation_data and self.parent_segment_usable_for_rotation_data
        )
        self.usable_translation_data = (
            self.child_segment_usable_for_translation_data and self.parent_segment_usable_for_translation_data
        )

        # todo: risk level implementation
        # self.rotation_risk = Risk.LOW
        # self.translation_risk = Risk.HIGH

        return self.usable_rotation_data, self.usable_translation_data

    def set_rotation_correction_callback(self):
        """
        The idea is to prepare a function ready to receive 3 Euler Angles (rot1, rot2, rot3) from any Euler Sequence,
        and from this sequence:
        - Rebuild the corresponding rotation matrix R_proximal_distal
        - Convert into a rotation matrix into x antero-posterior, y infero-superior, z medio-lateral (right)
        - Switch to a left-handed coordinate system
        if the data are on the left side to have the sign as for the right side on Euler angles
        - Apply a correction if any to make it ISB
        - Convert back into the Euler Sequence

        More mathematically:
        - 1st : R_proximal_distal = R(rot1, rot2, rot3, euler_sequence)
        - 2nd : R_proximal_distal = R_parent_correction @ R_distal_proximal @ R_child_correction (now z is medio-lateral)
        - 3rd if left side : R_proximal_distal = np.diag([1, 1, -1]) @ R_proximal_distal @ np.diag([1, 1, -1]) (now z is medio-lateral, for left side too)
        - 4th : R_proximal_distal = R_parent_correction @ R_proximal_distal @ R_child_correction
        - 5th : rot1, rot2, rot3 = euler_angles(R_proximal_distal, euler_sequence)

        """

        self.isb_rotation_matrix_callback = lambda rot1, rot2, rot3: isb_framed_rotation_matrix_from_euler_angles(
            rot1=rot1,
            rot2=rot2,
            rot3=rot3,
            previous_sequence_str=self.joint.euler_sequence.value,
            bsys_parent=self.parent_biomech_sys,
            bsys_child=self.child_biomech_sys,
        )

        if self.left_side:
            self.mediolateral_matrix = lambda rot1, rot2, rot3: to_left_handed_frame(
                maxtrix=isb_framed_rotation_matrix_from_euler_angles(
                    rot1=rot1,
                    rot2=rot2,
                    rot3=rot3,
                    previous_sequence_str=self.joint.euler_sequence.value,
                    bsys_parent=self.parent_biomech_sys,
                    bsys_child=self.child_biomech_sys,
                )
            )
        else:
            self.mediolateral_matrix = self.isb_rotation_matrix_callback

        parent_matrix_correction = (
            np.eye(3)
            if self.parent_corrections is None
            else get_kolz_rotation_matrix(correction=self.parent_corrections[0])
        )
        child_matrix_correction = (
            np.eye(3)
            if self.child_corrections is None
            else get_kolz_rotation_matrix(correction=self.child_corrections[0])
        )

        self.correct_isb_rotation_matrix_callback = lambda rot1, rot2, rot3: set_corrections_on_rotation_matrix(
            matrix=self.mediolateral_matrix(rot1, rot2, rot3),
            child_matrix_correction=child_matrix_correction,
            parent_matrix_correction=parent_matrix_correction,
        )

        self.euler_angles_correction_callback = lambda rot1, rot2, rot3: rotation_matrix_2_euler_angles(
            rotation_matrix=self.correct_isb_rotation_matrix_callback(rot1, rot2, rot3),
            euler_sequence=self.joint.isb_euler_sequence(),
        )

    def set_translation_correction_callback(self):
        """
        Work in Progress but here is the idea.

        I feel like we want to express the translation in the proximal segment coordinate system in ISB frame
        on the right side.

        It only fixes the ISB orientation, restoring x as antero-posterior, y as infero-superior, z as medio-lateral.
        and the side of the coordinate system left to right if needed.

        Missing features:
        - transport local to distal SCS ?

        """

        self.translation_isb_matrix_callback = (
            lambda trans_x, trans_y, trans_z: self.child_biomech_sys.get_rotation_matrix()
            @ np.array([[trans_x, trans_y, trans_z]]).T
        )

        if self.left_side:
            self.translation_mediolateral_matrix = (
                lambda trans_x, trans_y, trans_z: self.translation_isb_matrix_callback(trans_x, trans_y, trans_z)
                * np.array([1, 1, -1])
            )
        else:
            self.translation_mediolateral_matrix = self.translation_isb_matrix_callback

        # parent_matrix_correction = (
        #     np.eye(3)
        #     if self.parent_corrections is None
        #     else get_kolz_rotation_matrix(correction=self.parent_corrections[0])
        # )
        # child_matrix_correction = (
        #     np.eye(3)
        #     if self.child_corrections is None
        #     else get_kolz_rotation_matrix(correction=self.child_corrections[0])
        # )
        #
        # self.correct_isb_rotation_matrix_callback = lambda rot1, rot2, rot3: set_corrections_on_rotation_matrix(
        #     matrix=self.mediolateral_matrix(rot1, rot2, rot3),
        #     child_matrix_correction=child_matrix_correction,
        #     parent_matrix_correction=parent_matrix_correction,
        # )
        #
        # self.euler_angles_correction_callback = lambda rot1, rot2, rot3: rotation_matrix_2_euler_angles(
        #     rotation_matrix=self.correct_isb_rotation_matrix_callback(rot1, rot2, rot3),
        #     euler_sequence=self.joint.isb_euler_sequence(),

    def quantify_segment_risk(self, type_risk: str):
        """
        Quantify the risk of the joint.
        """
        risk_parent = self.parent_biomech_sys.get_segment_risk_quantification("proximal", type_risk)
        risk_child = self.child_biomech_sys.get_segment_risk_quantification("distal", type_risk)

        return risk_parent * risk_child

    def is_joint_euler_angle_ISB_with_adaptation_from_segment(self):
        """
        Check if the joint euler angle is ISB with adaptation from segment.

        To do it we use the fact that the mediolat, inferosup and anteropost axis of the parent and child segment are
        accessible through the biomech_sys object. As we know the equivalent between the anatomical axis and the ISB
        axis we can deduce the adapted euler sequence that should have been used in the article if it was respecting
        the ISB.

        Returns
        is_sequence_isb: bool
        """
        # We extract the euler sequences as found in the article and associated with the original segment definition
        raw_euler_seq = self.joint.euler_sequence.value
        # We extract the supposed euler sequence from the joint type
        supposed_euler_seq = EulerSequence.isb_from_joint_type(self.joint.joint_type).value
        # We know that in supposed_euler_seq
        # Z is supposed to be the +mediolat (point right)
        # Y is supposed to be the +inferosup (point up )
        # X is supposed to be the +anteropost (point front)

        # We should now check for the two first direction of the rotation the associated axis with
        # the parent segment (distal segment)
        adapted_euler_seq = ""
        for charac in supposed_euler_seq.lower()[0:2]:
            # TODO : probably put this in the biomech_sys function
            if charac == "x":
                adapted_euler_seq += self.parent_biomech_sys.anterior_posterior_axis.value[0]
            elif charac == "y":
                adapted_euler_seq += self.parent_biomech_sys.infero_superior_axis.value[0]
            elif charac == "z":
                adapted_euler_seq += self.parent_biomech_sys.medio_lateral_axis.value[0]

        # We should now check for the last direction of the rotation the associated axis with
        # the child segment (proximal segment)
        if supposed_euler_seq.lower()[2] == "x":
            adapted_euler_seq += self.child_biomech_sys.anterior_posterior_axis.value[0]
        elif supposed_euler_seq.lower()[2] == "y":
            adapted_euler_seq += self.child_biomech_sys.infero_superior_axis.value[0]
        elif supposed_euler_seq.lower()[2] == "z":
            adapted_euler_seq += self.child_biomech_sys.medio_lateral_axis.value[0]
        # Now the adapted euler_seq is the euler sequence that should have been used in the article if it was respecting
        # the ISB recomendation. So we can compare it to the raw euler sequence which has been used in the article.

        # We remove all the minus ("-") sign in the adapted euler sequence as the orientation error is already taken into account
        # in the deviation calculation.
        adapted_euler_seq.replace("-", "")

        is_sequence_isb = adapted_euler_seq == raw_euler_seq
        return is_sequence_isb

    def import_data(self):
        """this function import the data of the following row"""
        # todo: translation
        print(
            f" Importing data ...\n"
            f" for article {self.row.dataset_authors},"
            f" joint {self.row.joint},"
            f" motion {self.row.humeral_motion},"
            f" subject {self.row.shoulder_id}"
        )
        # load the csv file
        self.csv_filenames = self.get_euler_csv_filenames()
        self.data = load_euler_csv(self.csv_filenames)
        self.data["article"] = self.row.dataset_authors
        self.data["joint"] = JointType.from_string(self.row.joint)
        self.data["humeral_motion"] = self.row.humeral_motion

    def to_angle_series_dataframe(self, correction: bool = True):
        """
        This converts the row to a panda dataframe with the angles in degrees with the following columns:
         - article
         - joint
         - angle_translation
         - degree_of_freedom
         - movement
         - humerothoracic_angle (one line per angle)
         - value

        Returns
        -------
        pandas.DataFrame
            The dataframe with the angles in degrees
        """

        angle_series_dataframe = pd.DataFrame(
            columns=[
                "article",  # string
                "joint",  # string
                "humeral_motion",  # string
                "humerothoracic_angle",  # float
                "value_dof1",  # float
                "value_dof2",  # float
                "value_dof3",  # float
                "unit",  # string "angle" or "translation"
                "confidence",  # float
                "shoulder_id",  # int
                "in_vivo",  # bool
                "xp_mean",  # string
            ],
        )

        confidence_total = Deviation.confidence_total(row_data=self, type_risk="rotation")
        # TODO : detect if this is angle or translation

        value_dof = np.zeros((self.data.shape[0], 3))

        if correction:
            for i, row in enumerate(self.data.itertuples()):
                deg_corrected_dof_1, deg_corrected_dof_2, deg_corrected_dof_3 = self.apply_correction_in_radians(
                    row.value_dof1, row.value_dof2, row.value_dof3
                )
                value_dof[i, 0] = deg_corrected_dof_1
                value_dof[i, 1] = deg_corrected_dof_2
                value_dof[i, 2] = deg_corrected_dof_3

            # unwrap the angles to avoid discontinuities between -180 and 180 for example
            for i in range(0, 3):
                value_dof[:, i] = np.unwrap(value_dof[:, i], period=180)
        else:
            value_dof[:, 0] = self.data["value_dof1"].values
            value_dof[:, 1] = self.data["value_dof2"].values
            value_dof[:, 2] = self.data["value_dof3"].values

        angle_series_dataframe["value_dof1"] = value_dof[:, 0]
        angle_series_dataframe["value_dof2"] = value_dof[:, 1]
        angle_series_dataframe["value_dof3"] = value_dof[:, 2]
        angle_series_dataframe["article"] = self.row.dataset_authors
        angle_series_dataframe["joint"] = self.row.joint
        angle_series_dataframe["humeral_motion"] = self.row.humeral_motion
        angle_series_dataframe["humerothoracic_angle"] = self.data["humerothoracic_angle"]
        angle_series_dataframe["unit"] = "rad"
        angle_series_dataframe["confidence"] = confidence_total
        angle_series_dataframe["shoulder_id"] = self.row.shoulder_id
        angle_series_dataframe["in_vivo"] = self.row.in_vivo
        angle_series_dataframe["xp_mean"] = self.row.experimental_mean

        if correction:
            (legend_dof1, legend_dof2, legend_dof3) = self.joint.isb_rotation_biomechanical_dof
        else:
            legend_dof1, legend_dof2, legend_dof3 = (
                self.joint.euler_sequence.value[0],
                self.joint.euler_sequence.value[1],
                self.joint.euler_sequence.value[2],
            )

        legend_df = pd.DataFrame(
            {
                "degree_of_freedom": ["value_dof1", "value_dof2", "value_dof3"],
                "biomechanical_dof": [legend_dof1, legend_dof2, legend_dof3],
            }
        )

        self.corrected_data = angle_series_dataframe
        self.melted_data = angle_series_dataframe.melt(
            id_vars=[
                "article",
                "joint",
                "humeral_motion",
                "humerothoracic_angle",
                "unit",
                "confidence",
                "shoulder_id",
                "in_vivo",
                "xp_mean",
            ],
            value_vars=["value_dof1", "value_dof2", "value_dof3"],
            var_name="degree_of_freedom",
            value_name="value",
        )
        self.melted_data = pd.merge(self.melted_data, legend_df, on="degree_of_freedom")
        self.melted_data["degree_of_freedom"] = self.melted_data["degree_of_freedom"].replace(
            {"value_dof1": 1, "value_dof2": 2, "value_dof3": 3}
        )
        return self.melted_data

    def get_euler_csv_filenames(self) -> tuple[str, str, str]:
        """load the csv filenames from the row data"""
        folder_path = DataFolder.from_string(self.row["folder"]).value

        csv_paths = ()

        for field in [
            "dof_1st_euler",
            "dof_2nd_euler",
            "dof_3rd_euler",
        ]:
            csv_paths += (os.path.join(folder_path, self.row[field]),) if self.row[field] is not None else (None,)

        return csv_paths

    def get_translation_csv_filenames(self) -> tuple[str, str, str]:
        """load the csv filenames from the row data"""
        folder_path = DataFolder.from_string(self.row["folder"]).value

        csv_paths = ()

        for field in [
            "dof_translation_x",
            "dof_translation_y",
            "dof_translation_z",
        ]:
            csv_paths += (os.path.join(folder_path, self.row[field]),) if self.row[field] is not None else (None,)

        return csv_paths

    def apply_correction_in_radians(self, dof1, dof2, dof3) -> tuple[float, float, float]:
        """Apply the correction to the angles in radians"""

        rad_value_dof1 = np.deg2rad(dof1)
        rad_value_dof2 = np.deg2rad(dof2)
        rad_value_dof3 = np.deg2rad(dof3)

        corrected_dof_1, corrected_dof_2, corrected_dof_3 = self.euler_angles_correction_callback(
            rad_value_dof1, rad_value_dof2, rad_value_dof3
        )

        deg_corrected_dof_1 = np.rad2deg(corrected_dof_1)
        deg_corrected_dof_2 = np.rad2deg(corrected_dof_2)
        deg_corrected_dof_3 = np.rad2deg(corrected_dof_3)

        return deg_corrected_dof_1, deg_corrected_dof_2, deg_corrected_dof_3
