# mechanics

Source of the mechanical layer. Numbers are in `cad/params.py`; `cad/assemble.py` builds the model from it.

- `cad/`: build123d code, numbers in `mechanics/cad/params.py`
- `kinematics/`: appears when parts move (which nodes each actuator moves; derived from the AAS, never a second copy of numbers).
- Part designs (drawings, notes per part) go here too, one folder per part when needed.
- Generated from this layer, always into `../build/`: STL/3MF (print), DXF (laser), glTF (web), renders. Never commit them.
