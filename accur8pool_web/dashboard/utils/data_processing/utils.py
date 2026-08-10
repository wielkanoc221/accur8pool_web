import numpy as np
import pandas as pd
from scipy.signal import filtfilt, butter
from .const import *


def calc_pitch(acc_x_list, acc_y_list, acc_z_list, gyr_y_list, dt_list, alpha):
    pitch = []
    iterations = min(
        len(acc_x_list),
        len(acc_y_list),
        len(acc_z_list),
        len(gyr_y_list),
        len(dt_list)
    )

    pitch_gyr = np.arctan2(
        -acc_x_list[0],
        np.sqrt(acc_y_list[0] ** 2 + acc_z_list[0] ** 2)
    )

    for index in range(iterations):
        acc_x = acc_x_list[index]
        acc_y = acc_y_list[index]
        acc_z = acc_z_list[index]
        gyr_y = gyr_y_list[index]
        dt = dt_list[index]

        pitch_acc = np.arctan2(-acc_x, np.sqrt(acc_y ** 2 + acc_z ** 2))
        pitch_gyr = pitch_gyr + gyr_y * dt
        pitch_gyr = alpha * pitch_gyr + (1 - alpha) * pitch_acc

        pitch.append(pitch_gyr)

    return pitch


def calc_roll(acc_y, acc_z, gyr_x, dt, alpha=0.98):
    roll = []

    roll_gyr = np.arctan2(acc_y[0], acc_z[0])

    for i in range(len(acc_y)):
        roll_acc = np.arctan2(acc_y[i], acc_z[i])

        # gyro integration
        roll_gyr = roll_gyr + gyr_x[i] * dt[i]

        # complementary filter
        roll_gyr = alpha * roll_gyr + (1 - alpha) * roll_acc

        roll.append(roll_gyr)

    return roll


def calc_yaw_complementary(
        mag_x_list,
        mag_y_list,
        mag_z_list,
        gyr_z_list,
        roll_list,
        pitch_list,
        deltatime_list,
        alpha=0.98
):
    def wrap_angle_pi(angle):
        return (angle + np.pi) % (2 * np.pi) - np.pi

    yaw_est = 0.0
    yaw_list = []

    iterations = min(
        len(mag_x_list),
        len(mag_y_list),
        len(mag_z_list),
        len(gyr_z_list),
        len(roll_list),
        len(pitch_list),
        len(deltatime_list)
    )

    for i in range(iterations):
        mx = mag_x_list[i]
        my = mag_y_list[i]
        mz = mag_z_list[i]
        gz = gyr_z_list[i]
        roll = roll_list[i]
        pitch = pitch_list[i]
        dt = deltatime_list[i]

        # 1. Integracja żyroskopu
        yaw_gyro = yaw_est + gz * dt

        # 2. Tilt compensation magnetometru
        mx_comp = mx * np.cos(pitch) + mz * np.sin(pitch)

        my_comp = (
                mx * np.sin(roll) * np.sin(pitch)
                + my * np.cos(roll)
                - mz * np.sin(roll) * np.cos(pitch)
        )

        # 3. Yaw z magnetometru
        yaw_mag = np.arctan2(-my_comp, mx_comp)

        # 4. Filtr komplementarny
        # ważne: różnicę kątów też zawijamy
        error = wrap_angle_pi(yaw_mag - yaw_gyro)
        yaw_est = wrap_angle_pi(yaw_gyro + (1 - alpha) * error)

        yaw_list.append(yaw_est)

    return yaw_list


def _normalize(signal: np.ndarray) -> np.ndarray:
    arr = np.asarray(signal, dtype=float)
    min_val = np.min(arr)
    max_val = np.max(arr)
    if np.isclose(min_val, max_val):
        return np.zeros_like(arr)
    return (arr - min_val) / (max_val - min_val)


def _smooth(signal, window=5):
    arr = np.asarray(signal, dtype=float)

    if arr.size < 3:
        return arr

    window = max(3, window)
    if window % 2 == 0:
        window += 1

    cumsum = np.cumsum(np.insert(arr, 0, 0))
    return (cumsum[window:] - cumsum[:-window]) / window


def calc_jerk(acc_x_list, acc_y_list, acc_z_list, dt_list):
    magnitude = calc_magnitude(acc_x_list, acc_y_list, acc_z_list)
    magnitude = np.array(magnitude)
    dt_array = np.array(dt_list)

    # Obliczamy różnice między kolejnymi próbkami
    jerk = np.zeros_like(magnitude)
    # (mag[i] - mag[i-1]) / dt[i]
    jerk[1:] = (magnitude[1:] - magnitude[:-1]) / dt_array[1:]
    return jerk


def calc_magnitude(acc_x_list, acc_y_list, acc_z_list):
    ax = np.array(acc_x_list)
    ay = np.array(acc_y_list)
    az = np.array(acc_z_list)
    return np.sqrt(ax ** 2 + ay ** 2 + az ** 2)


def get_df_from_csv(path):
    df = pd.read_csv(path)
    return df


def lowpass_filter(data, cutoff=8, fs=100):
    b, a = butter(2, cutoff / (fs / 2), btype='low')
    return filtfilt(b, a, data, axis=0)


if __name__ == '__main__':
    import plotly.express as px

    df = get_df_from_csv(r'/data1\wolneuderzeniewbutle.csv')
    mag = calc_magnitude(df[ACC_X], df[ACC_Y], df[ACC_Z])
    fil_mag = lowpass_filter(mag, cutoff=20)
    df_plot = pd.DataFrame({'fil_mag': fil_mag, 'mag': mag})
    px.line(df_plot, y=['fil_mag', 'mag'], labels={'value': 'Magnitude', 'index': 'Sample'}).show()
