# from __future__ import annotations
#
# import pandas as pd
#
# from .dataWindowing import DataWindowing, CenterLabelingStrategy, extractWindowFeatures
#
#
# class MlDataProcessor:
#     def __init__(self, window_size=100, window_step=5, label_window_threshold=0.7):
#         self.window_size = window_size
#         self.window_step = window_step
#         self.label_window_threshold = label_window_threshold
#
#     def prepare_to_ml(self, X_data: pd.DataFrame, y_data: pd.Series):
#         y_data = y_data.fillna(0)
#         windowing = DataWindowing(window_size=self.window_size, window_step=self.window_step,
#                                   window_labeling_strategy=CenterLabelingStrategy())
#         X_data_np = X_data.to_numpy()
#         y_data_np = y_data.to_numpy()
#         data_windowed, labels_windowed, windows_metadata = windowing.window(X_data_np, y_data_np)
#         window_feature = extractWindowFeatures()
#         featured_windows = window_feature.extract(data_windowed)
#         return featured_windows, labels_windowed, windows_metadata

