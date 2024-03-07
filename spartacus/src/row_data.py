import os

import numpy as np
import pandas as pd

from .joint import Joint
from .biomech_system import BiomechCoordinateSystem

from .enums import Segment, Correction, DataFolder, EulerSequence, BiomechDirection, BiomechOrigin, JointType
from .utils import (
    get_segment_columns,
    get_correction_column,
    get_is_correctable_column,
    get_is_isb_column,
)

from .angle_conversion_callbacks import (
    get_angle_conversion_callback_from_tuple,
    get_angle_conversion_callback_from_sequence,
    get_angle_conversion_callback_to_isb_with_sequence,
    isb_framed_rotation_matrix_from_euler_angles,
    set_corrections_on_rotation_matrix,
    rotation_matrix_2_euler_angles,
)
from .kolz_matrices import get_kolz_rotation_matrix

from .checks import (
    check_segment_filled_with_nan,
    check_is_isb_segment,
    check_is_euler_sequence_provided,
    check_is_translation_provided,
    check_parent_child_joint,
    check_same_orientation,
    check_is_isb_correctable,
    check_correction_methods,
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

        self.rotation_correction_callback = None
        self.translation_correction_callback = None

        self.csv_filenames = None
        self.data = None
        self.corrected_data = None
        self.melted_corrected_data = None

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
                        f"{self.row.article_author_year}, "
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
                translation_frame=Segment.from_string(self.row.displacement_cs),
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
                translation_frame=Segment.from_string(self.row.displacement_cs),
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
        Set the rotation correction callback, for the joint. We rely on the corrections set in the table.
        """

        self.isb_rotation_matrix_callback = lambda rot1, rot2, rot3: isb_framed_rotation_matrix_from_euler_angles(
            rot1=rot1,
            rot2=rot2,
            rot3=rot3,
            previous_sequence_str=self.joint.euler_sequence.value,
            bsys_parent=self.parent_biomech_sys,
            bsys_child=self.child_biomech_sys,
        )

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
            matrix=self.isb_rotation_matrix_callback(rot1, rot2, rot3),
            child_matrix_correction=child_matrix_correction,
            parent_matrix_correction=parent_matrix_correction,
        )

        self.rotation_correction_callback = lambda rot1, rot2, rot3: rotation_matrix_2_euler_angles(
            rotation_matrix=self.correct_isb_rotation_matrix_callback(rot1, rot2, rot3),
            euler_sequence=self.joint.isb_euler_sequence(),
        )

    def quantify_segment_risk(self):
        """
        Quantify the risk of the joint.
        """
        risk_proximal = 1
        risk_distal = 1
        self.parent_biomech_sys.is_isb_oriented()

        if self.parent_biomech_sys.is_isb_oriented():
            risk_proximal = risk_proximal * 1
        else:
            if self.parent_biomech_sys





    def import_data(self):
        """this function import the data of the following row"""
        # todo: translation

        # load the csv file
        self.csv_filenames = self.get_euler_csv_filenames()
        self.data = load_euler_csv(self.csv_filenames)
        self.data["article"] = self.row.article_author_year
        self.data["joint"] = JointType.from_string(self.row.joint)
        self.data["humeral_motion"] = self.row.humeral_motion

    def to_angle_series_dataframe(self):
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

        corrected_angle_series_dataframe = pd.DataFrame(
            columns=[
                "article",
                "joint",
                "angle_translation",
                "humeral_motion",
                "humerothoracic_angle",
                "value_dof1",
                "value_dof2",
                "value_dof3",
            ],
        )

        for i, row in self.data.iterrows():
            corrected_dof_1, corrected_dof_2, corrected_dof_3 = self.rotation_correction_callback(
                row.value_dof1, row.value_dof2, row.value_dof3
            )

            # populate the dataframe
            corrected_angle_series_dataframe.loc[i] = [
                self.row.article_author_year,
                self.row.joint,
                "angle",
                self.row.humeral_motion,
                row.humerothoracic_angle,
                corrected_dof_1,
                corrected_dof_2,
                corrected_dof_3,
            ]

        self.corrected_data = corrected_angle_series_dataframe
        self.melted_corrected_data = corrected_angle_series_dataframe.melt(
            id_vars=["article", "joint","angle_translation", "humeral_motion", "humerothoracic_angle"],
            value_vars=["value_dof1", "value_dof2", "value_dof3"],
            var_name="degree_of_freedom",
            value_name="value",
        )
        self.melted_corrected_data["degree_of_freedom"] = self.melted_corrected_data["degree_of_freedom"].replace(
            {"value_dof1": "1", "value_dof2": "2", "value_dof3": "3"}
        )
        return self.melted_corrected_data

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


def load_euler_csv(csv_filenames: tuple[str, str, str], drop_humerothoracic_raw_data: bool = True) -> pd.DataFrame:
    """
    Load the csv file from the filename and return a pandas dataframe.
    """
    df = pd.DataFrame(columns=["humerothoracic_angle"])

    csv_file_dof1 = load_csv(
        csv_filenames[0],
        [
            "humerothoracic_angle_dof1",
            "value_dof1",
        ],
    )

    csv_file_dof2 = load_csv(
        csv_filenames[1],
        [
            "humerothoracic_angle_dof2",
            "value_dof2",
        ],
    )

    csv_file_dof3 = load_csv(
        csv_filenames[2],
        [
            "humerothoracic_angle_dof3",
            "value_dof3",
        ],
    )

    concatenated_dataframe = pd.concat([df, csv_file_dof1, csv_file_dof2, csv_file_dof3], axis=1)

    # mean of this three columns
    # assuming we should have the same value, this should minimize the error when collecting the data from figure.
    concatenated_dataframe["humerothoracic_angle"] = concatenated_dataframe[
        ["humerothoracic_angle_dof1", "humerothoracic_angle_dof2", "humerothoracic_angle_dof3"]
    ].mean(axis=1)

    if drop_humerothoracic_raw_data:
        concatenated_dataframe.drop(
            columns=["humerothoracic_angle_dof1", "humerothoracic_angle_dof2", "humerothoracic_angle_dof3"],
            inplace=True,
        )

    return concatenated_dataframe


def load_csv(csv_filenames, columns):
    if csv_filenames is not None:
        csv_file_dof1 = pd.read_csv(csv_filenames, sep=",", header=None)
        csv_file_dof1.columns = columns
    else:
        csv_file_dof1 = pd.DataFrame(columns=columns)

    return csv_file_dof1
