%
O1100(SLOT_IN_VISE)
( Units: INCHES)
( Tool Number: 1)
( Tool:   End Mill)
( DIAMETER: 0.5)
( Number of Flutes: 4)
( FLUTE LENGTH: 1.0)
( STICKOUT: 1.5)
( Tool Holder Diameter: 1.25)
( MIN X: 0.)
( MIN Y: 0.)
( MIN Z: -1.)
( MAX X: 4.)
( MAX Y: 2.)
( MAX Z: 0.)
G20 G17 G90
T1 M6
S3000 M3
G0 X-0.5 Y1.0
G43 H1 Z0.1
(FULL-WIDTH SLOT, 0.25 DEEP)
G1 Z-0.25 F20.
G1 X4.5 F12.
G0 Z0.1
M5
G91 G28 Z0.
M30
%
