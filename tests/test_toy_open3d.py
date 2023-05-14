import numpy as np
from toy_open3d_mesh import reduce_z_values



def test_reduce_z_values():
    # Create a test array
    array = np.array([[1, 2, 3, 4],
                      [5, 6, 7, 8],
                      [9, 10, 11, 12],
                      [13, 14, 15, 16]])

    # Define the center, diameter, and offset
    center = (1, 1)
    diameter = 2
    offset = 5

    # Call the function
    reduce_z_values(array, center, diameter, offset)

    # Verify the modified array
    expected_result = np.array([[1, 2, 3, 4],
                                [5, 5, 5, 8],
                                [9, 10, 11, 12],
                                [13, 14, 15, 16]])
    np.testing.assert_array_equal(array, expected_result)