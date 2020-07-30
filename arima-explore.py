################################
# ARIMA DEVELOP AND DIAGNOSTIC #
################################
# This code takes raw data and corrected data, applies an ARIMA time series model, identifies anomalies, outputs metrics

# print("ARIMA exploration script begin.")
import os
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as api
from statsmodels.graphics.tsaplots import plot_acf, plot_pacf

from pandas.plotting import register_matplotlib_converters
# register_matplotlib_converters()
# plt.rcParams.update({'figure.figsize':(9,7), 'figure.dpi':120})


def get_data(site, sensor, year, path=""):
    """Imports a single year of data based on files named by site, sensor/variable, and year.
    Labels data as anomalous. Generates a series from the data frame."""
    # TODO: make sensors input argument a list and output df with multiple normal_lbl columns.
    if path == "":
        path = os.getcwd() + "/"
    df = pd.read_csv(path + site + str(year) + ".csv",
                     skipinitialspace=True,
                     engine='python',
                     header=0,
                     index_col=0,
                     parse_dates=True,
                     infer_datetime_format=True)
    # makes one-dimensional data frame of booleans based on qualifier column indicating normal (TRUE) or not (FALSE)
    normal_lbl = df[sensor + "_qual"].isnull()
    # generate series from dataframe - time indexed values
    srs = pd.Series(df[sensor])

    return df, normal_lbl, srs


def set_threshold(predict, alpha_in):
    """This function gets called in the arima_diagnose_detect function.
    Uses SARIMAX model predict object to determine threshold based on confidence interval and
    specified alpha level of confidence."""
    predict_ci = predict.conf_int(alpha=alpha_in)
    predict_ci.columns = ["lower", "upper"]
    residuals[0][0] = 0
    predictions[0][0] = srs[0]
    predict_ci["lower"][0] = predict_ci["lower"][1]

    # this gives a constant interval for all points. Considering methods to vary the threshold with data/model
    # variability include using the PI with a SSE over just a previous window or scaling this threshold to a
    # past window SSE.
    # Could also try a threshold to maximize F2, but that requires having labeled data. Could base on a portion of data?
    thresholds = predictions[0] - predict_ci["lower"]
    threshold = thresholds[-1]

    return threshold


def arima_diagnose_detect(srs, p, d, q, summary, alpha="", threshold=""):
    """Builds an ARIMA model, determines threshold levels, identifies anomalies."""
    # BUILD MODEL
    model = api.tsa.statespace.SARIMAX(srs, order=(p, d, q))
    model_fit = model.fit(disp=0)
    # find residual errors and model predictions
    residuals = pd.DataFrame(model_fit.resid)
    predict = model_fit.get_prediction()
    predictions = pd.DataFrame(predict.predicted_mean)

    # set threshold
    if threshold == "":
        threshold = set_threshold(predict, alpha)

    # DETERMINE ANOMALIES
    anomDetn = np.abs(residuals) > threshold  # gives bools
    anomDetn[0][0] = False  # set 1st value to false

    # output summary
    if summary:
        print('\n\n')
        print(model_fit.summary())
        model_fit.plot_predict(dynamic=False)
        print('\n\nresiduals description:')
        print(residuals.describe())
        print('\nratio of detections: %f' % ((sum(anomDetn[0])/len(srs))*100), '%')

    return threshold, model_fit, residuals, predictions, anomDetn


def determine_events(normal_lbl):
    """Input to the windowing function. Searches through expert labeled data and counts groups of immediately
    consecutively labeled data points as anomalous events."""
    # TODO: uses +- 1 to widen window before and after the event. Should make this a parameter.
    # TODO: if an event is > some percent of the data, output a warning.
    event_count = 0
    events = []
    events.append(0)
    for i in range(1, len(normal_lbl)):
       if not(normal_lbl[i]):
        if normal_lbl[i-1]:
          event_count += 1
          events[i-1] = event_count
        events.append(event_count)
       else:
        if not(normal_lbl[i-1]):
          events.append(event_count)
        else:
          events.append(0)

    return events


def determine_detections(anomDetn):
    """Input to the windowing function. Searches through detected events and counts groups of immediately
    consecutively labeled data points as anomalous events."""
    # TODO: merge this function into determine_events
    det_count = 0
    det_events = []
    det_events.append(0)
    for i in range(1, len(anomDetn)):
      if anomDetn[0][i]:  # is a detection
        if not(anomDetn[0][i-1]):  # prev is not detection
          det_count += 1
          det_events[i-1] = det_count
        det_events.append(det_count)  # append detection number
      else:  # is not a detection
        if anomDetn[0][i-1]:  # prev is a detection
          det_events.append(det_count)  # append det number
        else:
          det_events.append(0)  # not a detection

    return det_events


def compare_lbl_detns(anomLbl, anomDetn, anomDetns):
    """Input to the windowing function. Compares the widened/windowed events between labels and detections."""
    # TODO: Understand need for and difference between anomDetn and anomDetns
    # generate lists of detected anomalies and valid detections
    detected_anomalies = [0]
    valid_detections = [0]
    for i in range(0, len(anomDetn)):
      if anomDetn[0][i]: # anomaly detected
        if 0!=anomLbl[0][i]: # labeled as anomaly
          if not(detected_anomalies[-1] == anomLbl[0][i]):  # if not already in list of detected anomalies
            detected_anomalies.append(anomLbl[0][i])
          if not(valid_detections[-1] == anomDetns[0][i]):  # if not already in list of valid detections
            valid_detections.append(anomDetns[0][i])

    detected_anomalies.pop(0)
    valid_detections.pop(0)

    return anomDetns, detected_anomalies, valid_detections


def false_detections(anomDetns, valid_detections):
    """Input to the windowing function. Determines detected events that were not labeled events."""
    invalid_detections = []
    det_ind = 0
    for i in range(1, max(anomDetns[0])):
        if (det_ind < len(valid_detections)):
            if i == valid_detections[det_ind]:
                det_ind += 1
            else:
                invalid_detections.append(i)

    return invalid_detections


def windowing(srs, normal_lbl, anomDetn):
    """Widens the window before or after a detection for assigning an anomaly label."""
    # create lists of labeled events and detected events. data frame is same length as full data series.
    label_events = determine_events(normal_lbl)
    anomLbl = pd.DataFrame(data=label_events, index=normal_lbl.index)
    # add index to data frame of individually detected anomalies. fix missing values
    anomDetn = anomDetn.reindex(anomLbl.index)
    anomDetn[anomDetn.isnull()] = True
    # determine detected events and reindex
    det_events = determine_detections(anomDetn)
    anomDetns = pd.DataFrame(data=det_events, index=normal_lbl.index)
    # compare labeled and detected events
    anomDetns, detected_anomalies, valid_detections = compare_lbl_detns(anomLbl, anomDetn, anomDetns)
    # TODO: check for empty detected_anomalies and valid_detections
    # determine detected but not labeled events
    invalid_detections = false_detections(anomDetns, valid_detections)

    return anomLbl, anomDetn, anomDetns, detected_anomalies, invalid_detections


def metrics(anomDetns, anomDetn, anomLbl, detected_anomalies, invalid_detections):
    """Calculates metrics for anomaly detection."""
    TruePositives = sum(anomLbl[0].value_counts()[detected_anomalies])
    FalseNegatives = len(anomDetn) - anomLbl[0].value_counts()[0] - TruePositives
    FalsePositives = sum(anomDetns[0].value_counts()[invalid_detections])
    TrueNegatives = len(anomDetn) - TruePositives - FalseNegatives - FalsePositives

    PRC = PPV = TruePositives / (TruePositives + FalsePositives)
    NPV = TrueNegatives / (TrueNegatives + FalseNegatives)
    ACC = (TruePositives + TrueNegatives) / len(anomDetn)
    RCL = TruePositives / (TruePositives + FalseNegatives)
    f1 = 2.0 * (PRC * RCL) / (PRC + RCL)
    f2 = 5.0 * TruePositives / (5.0 * TruePositives + 4.0 * FalseNegatives + FalsePositives)
    # ACC = (TruePositives+TrueNegatives)/(TruePositives+TrueNegatives+FalsePositives+FalseNegatives)

    return TruePositives, FalseNegatives, FalsePositives, TrueNegatives, PRC, PPV, NPV, ACC, RCL, f1, f2

#########################################
# IMPLEMENTATION AND FUNCTION EXECUTION #
#########################################

# DEFINE SITE and VARIABLE #
#########################################
# site = "BlackSmithFork"
# site = "FranklinBasin"
# site = "MainStreet"
site = "Mendon"
# site = "TonyGrove"
# site = "WaterLab"
# sensor = "temp"
sensor = "cond"
# sensor = "ph"
# sensor = "do"
# sensor = "turb"
# sensor = "stage"
year = 2017

# PARAMETER SELECTION #
#########################################
# Need to use an automated method to generalize getting p,d,q parameters
# These are the results of using auto.ARIMA to determine p,d,q parameters in R
sites = {'BlackSmithFork': 0,
         'FranklinBasin': 1,
         'MainStreet': 2,
         'Mendon': 3,
         'TonyGrove': 4,
         'WaterLab': 5}
sensors = {'cond': 0,
           'do': 1,
           'ph': 2,
           'temp': 3,
           'turb': 4}
pdqParams = [
            [[0,0,5],[0,0,5],[0,1,4],[1,1,0],[9,1,5]],  # BlackSmithFork
            [[10,1,3],[0,1,5],[10,1,1],[6,1,4],[0,1,5]],  # FranklinBasin
            [[1,1,5],[1,1,1],[3,1,1],[0,0,0],[1,1,5]],  # MainStreet
            [[9,1,4],[10,1,3],[0,1,2],[3,1,1],[9,1,1]],  # Mendon
            [[6,1,2],[10,1,0],[8,1,4],[10,1,0],[10,1,5]],  # TonyGrove
            [[7,1,0],[1,1,1],[10,1,0],[0,1,5],[1,1,3]]  # WaterLab
            ]
pdqParam = pd.DataFrame(pdqParams, columns=sensors, index=sites)
print(pdqParam)

p, d, q = pdqParam[sensor][site]
print("p: "+str(p))
print("d: "+str(d))
print("q: "+str(q))

# EXECUTE FUNCTIONS #
#########################################
df, normal_lbl, srs = get_data(site, sensor, year, path="/Users/amber/PycharmProjects/LRO-anomaly-detection/LRO_data/")
threshold, model_fit, residuals, predictions, anomDetn = arima_diagnose_detect(srs, p, d, q, alpha=0.5, summary=True)
anomLbl, anomDetn, anomDetns, detected_anomalies, invalid_detections = windowing(srs, normal_lbl, anomDetn)
TruePositives, FalseNegatives, FalsePositives, TrueNegatives, PRC, PPV, NPV, ACC, RCL, f1, f2 \
    = metrics(anomDetns, anomDetn, anomLbl, detected_anomalies, invalid_detections)

# OUTPUT RESULTS #
#########################################
print('\n\n\nScript report:\n')
print('Sensor: ' + sensor)
print('Year: ' + str(year))
print('Parameters: ARIMA(%i, %i, %i), Threshold = %f' %(p, d, q, threshold))
print('PPV = %f' % PPV)
print('NPV = %f' % NPV)
print('Acc = %f' % ACC)
print('TP  = %i' % TruePositives)
print('TN  = %i' % TrueNegatives)
print('FP  = %i' % FalsePositives)
print('FN  = %i' % FalseNegatives)
print('F1 = %f' % f1)
print('F2 = %f' % f2)
print("\nTime Series ARIMA script end.")


# GENERATE PLOTS #
#########################################
plt.figure()
plt.plot(srs, 'b', label='original data')
plt.plot(predictions, 'c', label='predicted values')
plt.plot(srs[~normal_lbl], 'mo', mfc='none', label='technician labeled anomalies')
plt.plot(predictions[anomDetn[0]],'r+', label='machine detected anomalies')
plt.legend()
plt.ylabel(sensor)
plt.show()



# EXPLORING SEASONAL ARIMA. Seems like seasonal would be useful given the diel fluctuation of ambient/aquatic variables.
# However, using 96 as the seasonal basis (4x24 hours) is computationally intensive.
# In parameter searching, seasonal variables were not significant?

# fig = api.graphics.tsa.plot_acf(srs.diff().dropna(), lags=700)
# plt.show()
# d = pm.arima.ndiffs(srs, test='adf')
# D = pm.arima.nsdiffs(srs, m=96, max_D=5)
# print("D = " + str(D)+"\nd = "+str(d)+"\n")
# model = pm.auto_arima(srs, start_p=1, start_q=1,
#     # test='adf', # use adftest to find optimal 'd'
#     max_p=3,
#     max_q=3,
#     seasonal=False,
#     # seasonal=True, # daily seasonality
#     # m=96, # number of samples per seasonal cycle, 4 samples/hr * 24hrs
#     d=0,
#     start_P=0,
#     # D=D,
#     trace=True,
#     error_action='ignore',
#     suppress_warnings=True,
#     stepwise=True)
# print(model.summary())

# initial exploration into parameter grid search
# p_vals = [0,1,2,3,4]
# d_vals = [0,1]
# q_vals = [0,1,2,3,4]
# for p in p_vals:
#   for d in d_vals:
#     for q in q_vals:
#       try:
#         order = (p,d,q)
#         model = ARIMA(srs, order)
#         model_fit = model.fit(disp=0)
#         print(model_fit)

# build ARIMA model


# Trying to use f score to maximize threshold
# def find_threshold(min, max, inc, normal_lbl, anomDetns):
#     f_score = []
#     thresholds = np.arange(min, max, inc) #set range and increments for threshold. will need to
#     generalize for other variables.
#     for threshold in thresholds:
#
#
#         f1 = metrics.f1_score(normal_lbl, anomDetns)
#         f_score.append(f1)
#
#     sns.lineplot(thresholds, f_score)
#     plt.xlabel("threshold")
#     plot.ylabel("f1 score")
#     opt_threshold =
#     return (thresholds, f_score, opt_threshold)
