# ZyenLang v0.1.47 Robotics / Control Stdlib

v0.1.47 adds a practical robotics/control helper layer on top of the existing math/control/PID modules.

## Modules

### `std/units`

Import:

```zy
import <std/units>;
```

Functions:

- `units.deg_to_rad(deg: float) -> float`
- `units.rad_to_deg(rad: float) -> float`
- `units.rpm_to_rad_s(rpm: float) -> float`
- `units.rad_s_to_rpm(rad_s: float) -> float`
- `units.mm_to_m(mm: float) -> float`
- `units.m_to_mm(m: float) -> float`
- `units.cm_to_m(cm: float) -> float`
- `units.m_to_cm(m: float) -> float`
- `units.g_to_kg(g: float) -> float`
- `units.kg_to_g(kg: float) -> float`
- `units.ms_to_s(ms: float) -> float`
- `units.s_to_ms(s: float) -> float`

### `std/filter`

Import:

```zy
import <std/filter>;
```

Functions:

- `filter.low_pass_float(prev, input, alpha)`
- `filter.high_pass_float(prev_output, input, prev_input, alpha)`
- `filter.moving_avg2_float(a, b)`
- `filter.moving_avg3_float(a, b, c)`
- `filter.moving_avg4_float(a, b, c, d)`
- `filter.deadband_float(value, band)`
- `filter.deadband_int(value, band)`
- `filter.slew_rate_limit_float(prev, target, max_delta)`
- `filter.slew_rate_limit_int(prev, target, max_delta)`
- `filter.threshold_bool(value, threshold)`
- `filter.hysteresis_bool(value, low, high, prev)`

### `std/trajectory`

Import:

```zy
import <std/trajectory>;
```

Functions:

- `trajectory.linear_float(start, end, t)`
- `trajectory.smoothstep_float(start, end, t)`
- `trajectory.step_int(current, target, step)`
- `trajectory.step_float(current, target, step)`
- `trajectory.ramp_float(current, target, rate_per_s, dt)`
- `trajectory.time_ratio(elapsed, duration)`
- `trajectory.position_at_time(start, end, elapsed, duration)`
- `trajectory.smooth_position_at_time(start, end, elapsed, duration)`

### `std/robot`

Import:

```zy
import <std/robot>;
```

Types:

- `Pose2D { x: float, y: float, theta: float }`
- `JointLimit { min: float, max: float }`

Functions:

- `robot.pose2d(x, y, theta) -> Pose2D`
- `robot.joint_limit(min, max) -> JointLimit`
- `robot.clamp_joint(angle, min, max) -> float`
- `robot.clamp_joint_limit(angle, limit) -> float`
- `robot.in_joint_limit(angle, min, max) -> bool`
- `robot.in_joint_limit_obj(angle, limit) -> bool`
- `robot.deg_to_rad(deg) -> float`
- `robot.rad_to_deg(rad) -> float`
- `robot.normalize_angle_rad(angle) -> float`
- `robot.normalize_angle_deg(angle) -> float`
- `robot.limit_velocity(v, max_v) -> float`
- `robot.limit_accel(a, max_a) -> float`
- `robot.apply_deadband(value, band) -> float`
- `robot.step_joint(current, target, max_delta) -> float`
- `robot.pose_translate(p, dx, dy) -> Pose2D`
- `robot.pose_rotate(p, dtheta) -> Pose2D`
- `robot.pose_distance(a, b) -> float`
- `robot.diff_drive_left(linear, angular, track_width) -> float`
- `robot.diff_drive_right(linear, angular, track_width) -> float`
- `robot.wheel_rad_s(linear_speed, wheel_radius) -> float`
- `robot.wheel_rpm(linear_speed, wheel_radius) -> float`

## Example

```zy
import <std/robot>;
import <std/filter>;
import <std/trajectory>;
import <std/units>;

fn main() -> int {
    let target: float = units.deg_to_rad(90.0);
    let current: float = units.deg_to_rad(12.0);

    let next: float = trajectory.ramp_float(
        current,
        target,
        units.deg_to_rad(45.0),
        0.1
    );

    let filtered: float = filter.low_pass_float(current, next, 0.5);

    print("next_deg=" + (str)units.rad_to_deg(filtered));

    return 0;
}
```
