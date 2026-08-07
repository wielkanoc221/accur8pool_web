ACC_X = 'accx'
ACC_Y = 'accy'
ACC_Z = 'accz'
LIN_ACC_X = 'linaccx'
LIN_ACC_Y = 'linaccy'
LIN_ACC_Z = 'linaccz'
GYR_X = 'gyrx'
GYR_Y = 'gyry'
GYR_Z = 'gyrz'
MAG_X = 'magx'
MAG_Y = 'magy'
MAG_Z = 'magz'
ROT_X = 'rotx'
ROT_Y = 'roty'
ROT_Z = 'rotz'
PITCH = 'pitch'
ROLL = 'roll'
YAW = 'yaw'
JERK = 'jerk'
JERK_ACC_X = 'jerk_accx'
JERK_ACC_Y = 'jerk_accy'
JERK_ACC_Z = 'jerk_accz'
JERK_MAG = 'jerk_MAG'
TIME = 'time'
ACC_MAGNITUDE = 'acc_magnitude'
GYR_MAGNITUDE = 'gyr_magnitude'
TIMESTAMP = 'timestamp'
LABEL = 'label'
SENSOR_FEATURES = [ACC_X, ACC_Y, ACC_Z, GYR_X, GYR_Y, GYR_Z, MAG_X, MAG_Y, MAG_Z,
                   LIN_ACC_X, LIN_ACC_Y, LIN_ACC_Z, ROT_X, ROT_Y, ROT_Z,
                   ACC_MAGNITUDE, GYR_MAGNITUDE, JERK_ACC_X, JERK_ACC_Y,
                   JERK_ACC_Z, JERK_MAG, ROLL, PITCH]

COLUMNS_TO_DEFAULT_NORMALIZE = ['accx', 'accy', 'accz', 'gyrx', 'gyry', 'gyrz', 'magx', 'magy', 'magz',
                                'linaccx', 'linaccy', 'linaccz', 'rotx', 'roty', 'rotz', 'acc_magnitude',
                                'gyr_magnitude', 'jerk_accx', 'jerk_accy',
                                'jerk_accz', 'jerk_MAG', 'roll', 'pitch', 'jerk_magnitude']

DATA_COLUMNS_TO_TRAIN = ['accx', 'accy', 'accz', 'gyrx', 'gyry', 'gyrz', 'magx', 'magy', 'magz',
       'linaccx', 'linaccy', 'linaccz', 'rotx', 'roty', 'rotz',
       'acc_magnitude', 'gyr_magnitude', 'jerk_accx', 'jerk_accy',
       'jerk_accz', 'acc_magnitude_jerk', 'jerk_gyrx', 'jerk_gyry',
       'jerk_gyrz', 'gyr_magnitude_jerk', 'roll', 'pitch']
