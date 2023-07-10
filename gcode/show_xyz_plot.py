#!/usr/bin/env python3
"""
Sample plotter for an XYZ file
"""
import sys
import matplotlib.pyplot as plt

def plot_xyz(filename):
    """
    Plots an XYZ file
    """
    # Lists to store X and Y coordinates
    x_coordinates = []
    y_coordinates = []
    z_coordinates = []

    # Read the file
    with open(filename, 'r') as file:
        for line in file:
            # Extract X and Y coordinates from each line
            data = line.strip().split()
            x = int(data[0])
            y = int(data[1])
            z = int(data[2])

            # Append X and Y coordinates to the lists
            x_coordinates.append(x)
            y_coordinates.append(y)
            z_coordinates.append(z)


    # Plot the X and Y coordinates
    print(list(zip(x_coordinates, y_coordinates,z_coordinates))[:5])
    # Plot the X, Y, and Z coordinates
    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')
    ax.scatter(x_coordinates, y_coordinates, z_coordinates, c='b', marker='o')

    # Set labels for X, Y, and Z axes
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')

    # Set plot title
    ax.set_title('3D Coordinate Plot')

    # Show the plot
    plt.show()

if __name__ == "__main__":
    plot_xyz(sys.argv[1])