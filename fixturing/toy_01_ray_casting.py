import open3d as o3d
import numpy as np


# http://www.open3d.org/docs/latest/tutorial/geometry/ray_casting.html

def ray_cast(mesh, start_point, direction):
    # Create a ray from the start_point in the specified direction
    ray = o3d.geometry.TriangleMesh.create_line(origin=start_point, direction=direction)

    # Perform ray-mesh intersection test
    result = o3d.geometry.TriangleMesh.ray_test(mesh, ray)

    # Find the intersection points and distances
    intersects = np.asarray(result[0])
    distances = np.asarray(result[1])

    if len(intersects) > 0:
        # Sort the intersection points by distance
        sorted_indices = np.argsort(distances)
        intersects = intersects[sorted_indices]
        distances = distances[sorted_indices]

        # Get the Z value of the closest intersection point
        closest_intersection = intersects[0]
        z_value = closest_intersection[2]

        return z_value
    else:
        # No intersection found, return a value indicating empty space (e.g., 0)
        return 0.0

# Load STL file and convert to mesh
mesh = o3d.io.read_triangle_mesh("kurt.stl")

# Calculate the dimensions of the 2D array

a = mesh.get_axis_aligned_bounding_box()
min_bound = a.min_bound
max_bound = a.max_bound


length_x_thousandths = int((max_bound[0] - min_bound[0]) * 1000)
length_y_thousandths = int((max_bound[1] - min_bound[1]) * 1000)

# Create a 2D array initialized to zero
array_2d = np.zeros((length_x_thousandths, length_y_thousandths))

# Iterate through each point in the array
for x in range(length_x_thousandths):
    for y in range(length_y_thousandths):
        # Calculate the corresponding coordinates in the mesh
        x_coord = (x / 1000.0) + min_bound[0]
        y_coord = (y / 1000.0) + min_bound[1]

        # Define a start point for the ray casting
        start_point = [x_coord, y_coord, max_bound[2] + 1.0]  # Ensuring the ray starts above the mesh

        # Specify the ray direction (e.g., pointing downwards)
        direction = [0, 0, -1]

        # Perform ray casting to find the Z value at the point
        z_value = ray_cast(mesh, start_point, direction)

        # Assign the Z value to the array
        array_2d[x, y] = z_value

# Now, 'array_2d' contains the desired 2D representation of the solid with Z values or 0 for empty space