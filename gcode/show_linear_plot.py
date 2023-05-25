import matplotlib.pyplot as plt
from linear import *
def plot_points(points):
    x_vals = [point[0] for point in points]
    y_vals = [point[1] for point in points]
    z_vals = [point[2] for point in points]

    fig = plt.figure()
    ax = fig.add_subplot(111, projection="3d")
    ax.plot(x_vals, y_vals, z_vals, marker='o', color='blue')
    ax.scatter(x_vals[0], y_vals[0], z_vals[0], marker='o', color='orange', label='Start')
    ax.scatter(x_vals[-1], y_vals[-1], z_vals[-1], marker='o', color='yellow', label='End')
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')
    ax.legend()
    plt.show()


# Generate points and plot the results
start = (0, 0, 0)
end = (1, 2, 3)
step_size = 0.5
points = generate_points(start, end, step_size)
plot_points(points)