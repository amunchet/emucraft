import sys
import open3d as o3d
import numpy as np
import time

start_time = time.time()


# http://www.open3d.org/docs/latest/tutorial/geometry/ray_casting.html

# TODO: Couple of things to try - we might want to bring in lots of Tensors for the entire area, then run cast_rays

# TODO: We might try to do Multi-threading

# TODO: Also might try CUDA


def ray_cast(mesh, rays):
    # Create a ray from the start_point in the specified direction

    """
    ray = o3d.geometry.TriangleMesh.create_line(origin=start_point, direction=direction)

    # Perform ray-mesh intersection test
    result = o3d.geometry.TriangleMesh.ray_test(mesh, ray)
    """
    # x,y,z = start_point
    scene = o3d.t.geometry.RaycastingScene()
    cube_id = scene.add_triangles(mesh)

    """
    rays = o3d.core.Tensor(
        [
            # [0.5, 0.5, 10, 0, 0, -1], 
            [x, y, z, 0, 0, -1], 
        ], dtype=o3d.core.Dtype.Float32
    )
    """
    ans = scene.cast_rays(rays)
    return ans

    """
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
    """

# Load STL file and convert to mesh
mesh = o3d.io.read_triangle_mesh("kurt.stl")
a = o3d.t.geometry.TriangleMesh()
mesh = a.from_legacy(mesh)

# Calculate the dimensions of the 2D array

a = mesh.get_axis_aligned_bounding_box()
min_bound = a.min_bound
max_bound = a.max_bound

div_factor = 39.37 # 1000 / 25.4 - I think this gives me .001" increments

length_x_thousandths = int((max_bound[0].item() - min_bound[0].item()) * div_factor)
length_y_thousandths = int((max_bound[1].item() - min_bound[1].item()) * div_factor)

# Create a 2D array initialized to zero
array_2d = np.zeros((length_x_thousandths, length_y_thousandths))


# Iterate through each point in the array


ray_arr = []

min_bound_x = min_bound[0].item()
min_bound_y = min_bound[1].item()
max_z = max_bound[2].item()


"""
for x in range(length_x_thousandths):
    print("X/X Length:", x, length_x_thousandths)
    for y in range(length_y_thousandths):
        # print("Y/Y length:", y, length_y_thousandths)
        # Calculate the corresponding coordinates in the mesh
        
        x_coord = (x / div_factor) + min_bound_x
        y_coord = (y / div_factor) + min_bound_y

        # Define a start point for the ray casting
        start_point = [
            x_coord,
            y_coord,
            max_z + 1.0,
        ]  # Ensuring the ray starts above the mesh
        # print("Start point:", start_point)
        # Specify the ray direction (e.g., pointing downwards)
        # direction = [0, 0, -1]

        # Perform ray casting to find the Z value at the point
        # z_value = ray_cast(mesh, start_point, direction)
        ray_arr.append([x_coord, y_coord, max_z + 1.0, 0, 0, -1])

        # z_value = ray_cast(mesh, start_point)['t_hit'].item()


        #print("Z_value:", z_value)
        # print("-------")
        # Assign the Z value to the array
        # array_2d[x,y] = z_value
"""


# Create arrays for x and y coordinates separately
x_coords = np.arange(0, length_x_thousandths) / div_factor + min_bound_x
y_coords = np.arange(0, length_y_thousandths) / div_factor + min_bound_y


print("X coords:", x_coords)
print("Y coords:", y_coords)

# Calculate the lengths of x_coords and y_coords
num_x_coords = len(x_coords)
num_y_coords = len(y_coords)

# Create arrays for start points (x, y, z)
start_x = np.repeat(x_coords, num_y_coords)
start_y = np.tile(y_coords, num_x_coords)
start_z = np.full_like(start_x, max_z + 1.0)

print("Start x,y,z:", start_x, start_y, start_z)

# Create an array for directions (0, 0, -1)
directions = np.column_stack((np.zeros_like(start_x), np.zeros_like(start_y), np.full_like(start_x, -1.0)))

# Combine start points and directions into ray_arr
ray_arr = np.column_stack((start_x, start_y, start_z, directions))

print("Ray arr:", ray_arr)



print("Out of main loop")
rays = o3d.core.Tensor(
    ray_arr, dtype=o3d.core.Dtype.Float32
)   
z_value = ray_cast(mesh, rays)



# Now, 'array_2d' contains the desired 2D representation of the solid with Z values or 0 for empty space

end_time = time.time()
print("Rough time:", end_time - start_time)