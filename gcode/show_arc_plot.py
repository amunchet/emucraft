import matplotlib.pyplot as plt
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.patches import Arc

import arc

def show_two_lines():
    # create data
    x = [10, 20, 30, 40, 50]
    y = [30, 30, 30, 30, 30]

    # plot lines
    plt.plot(x, y, label="line 1")
    plt.plot(y, x, label="line 2")
    plt.legend()
    plt.show()

def show_arc():

    fig = plt.figure(figsize=(5,5))
    ax = fig.add_subplot(1,1,1)
    ax.set_ylim(0, 50)
    ax.set_xlim(0, 50)
    # ax.axis('off')


    # Draw the bottom half of the ellipse (theta 180-360 deg).

    center_x = 10
    center_y = 10

    diameter = 10

    rotate_circle = 0 # I think this should be 0
    angle_2 = 30 # Start angle
    angle_3 = 90 # End angle

    # angle_2 > angle_3 = Counter clockwise
    # angle_3 > angle_2 = clockwise

    a = Arc((center_x, center_y), diameter, diameter, rotate_circle, angle_2, angle_3, color='red', lw=1)
    ax.add_patch(a)

    plt.show()

def show_points():
    """
    Draws some points

    """

    print("G2 normal")

    a = arc.segment(
        (0,100,0),
        (100,0,0),
        (0,-100,0),
        g3 = False,
        g2 = True 
    )


    a = [(x[0], x[1]) for x in a]
    print(a)

    fig = plt.figure(figsize=(5,5))
    ax = fig.add_subplot(1,1,1)
    ax.set_ylim(-110, 110)
    ax.set_xlim(-110, 110)

    fig.suptitle("G2 Normal (dark is first)", fontsize=14, fontweight="bold")

    #for x,y in a:
    #    plt.plot(x,y, marker="o", markersize=5, markerfacecolor="green")

    plt.scatter([x[0] for x in a], [x[1] for x in a], c=[i for i in range(0, len(a))], cmap="jet")
    
    plt.show()

    print("G3 Abnormal")

    a = arc.segment(
        (0,100,0),
        (100,0,0),
        (0,-100,0),
        g3 = True,
        g2 = False, 
    )


    a = [(x[0], x[1]) for x in a]
    print(a)

    fig = plt.figure(figsize=(5,5))
    fig.suptitle("G3 Abnormal", fontsize=14, fontweight="bold")
    ax = fig.add_subplot(1,1,1)
    ax.set_ylim(-110, 110)
    ax.set_xlim(-110, 110)

    #for x,y in a:
    #    plt.plot(x,y, marker="o", markersize=5, markerfacecolor="green")
    plt.scatter([x[0] for x in a], [x[1] for x in a], c=[i for i in range(0, len(a))], cmap="jet")
    plt.show()

    print("G3 Normal")


    a = arc.segment(
        (100,0,0),
        (0,100,0),
        (-100,0,0),
        g3 = True,
        g2 = False, 
    )


    a = [(x[0], x[1]) for x in a]
    print(a)

    fig = plt.figure(figsize=(5,5))
    fig.suptitle("G3 Normal", fontsize=14, fontweight="bold")
    ax = fig.add_subplot(1,1,1)
    ax.set_ylim(-110, 110)
    ax.set_xlim(-110, 110)

    #for x,y in a:
    #    plt.plot(x,y, marker="o", markersize=5, markerfacecolor="green")
    plt.scatter([x[0] for x in a], [x[1] for x in a], c=[i for i in range(0, len(a))], cmap="jet")
    plt.show()


    print("G2 Abnormal")
    a = arc.segment(
        (100,0,0),
        (0,100,0),
        (-100,0,0),
        g3 = False,
        g2 = True, 
    )


    a = [(x[0], x[1]) for x in a]
    print(a)

    fig = plt.figure(figsize=(5,5))
    fig.suptitle("G2 Abnormal", fontsize=14, fontweight="bold")
    ax = fig.add_subplot(1,1,1)
    ax.set_ylim(-110, 110)
    ax.set_xlim(-110, 110)

    #for x,y in a:
    #    plt.plot(x,y, marker="o", markersize=5, markerfacecolor="green")
    plt.scatter([x[0] for x in a], [x[1] for x in a], c=[i for i in range(0, len(a))], cmap="jet")

    plt.show()

if __name__ == "__main__":
    show_points()
    #show_two_lines()
    #show_arc()