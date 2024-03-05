import numpy as np
import pandas as pd
import plotly.graph_objects as go
from sklearn.cluster import AgglomerativeClustering, KMeans
from scipy import stats

import matplotlib.pyplot as plt

import util.common_funcs as common

def ranges_by_position(df, position):
    # df is obtained from LES, by dividing a 60-minute run into 6 intervals
    # and calculating dCpskew, dCpkurt for each. The range is then computed
    # for each position around the perimeter of the parapet.
    # df has columns Position | dCpskew_range | dCpkurt_range
    
    diff = np.abs(df['Position'] - position)
    idx = diff.idxmin()
    return df['dCpskew_range'].iloc[idx], df['dCpkurt_range'].iloc[idx]

def MC_ranges(rms, skewness_sign, N_samples):
    '''
    Given a distribution's truth rms (stdev) value, find the ranges
    of skewness and kurtosis given limited samples from this distribution
    '''
    N_repetitions = 1000

    sk = []
    ku = []
    for i in range(N_repetitions):
        samples = np.random.lognormal(0, rms, N_samples)
        sk.append(stats.skew(samples))
        ku.append(stats.kurtosis(samples, fisher=False))

    # esigmasq = np.exp(rms ** 2)
    # sk_true = np.sqrt(esigmasq - 1) * (esigmasq + 2)
    # ku_true = 3 + (esigmasq - 1) * (esigmasq ** 3 + 3 * esigmasq ** 2 + 6 * esigmasq + 6)
    # plt.plot(sk, ku, 'k.')
    # plt.plot(sk_true, ku_true, 'r+')
    # plt.show()

    # Get 95% ranges of sk, ku
    sk_range = np.multiply(skewness_sign, [np.percentile(sk, 2.5), np.percentile(sk, 97.5)])
    ku_range = np.array([np.percentile(ku, 2.5), np.percentile(ku, 97.5)])

    return sk_range, ku_range

def determine_clusters(df):
    X = np.column_stack((df['dCpskew'], df['dCpkurt']))
    model = AgglomerativeClustering(n_clusters=None, #int(np.floor(df.shape[0] / 16)) 
                                    distance_threshold=np.sqrt(2)).fit(X)
    # model = OPTICS(max_eps=10*np.sqrt(2)).fit(X)
    # model = HDBSCAN(min_cluster_size=16,
    #                 cluster_selection_epsilon=np.sqrt(2),
    #                 min_samples=10).fit(X)
    df['labels'] = model.labels_

def determine_clusters_alt(df, N_clusters):
    X = np.column_stack((df['dCpskew'], df['dCpkurt']))
    model = KMeans(n_clusters=N_clusters).fit(X)
    df['labels'] = model.labels_

def check_clusters(df, min_windows):
    bad_clusters = []
    for i in np.unique(df['labels']):
        x = df.loc[df['labels']==i,'dCpskew']
        y = df.loc[df['labels']==i,'dCpkurt']
        
        print('Cluster %d (N = %d) - dCpskew range: %.2f dCpkurt range: %.2f' %(i, len(x), np.ptp(x), np.ptp(y)))
        # If this cluster exceeds either max range and is still >16 elements, add it to the list
        if (np.ptp(x) > 1 or np.ptp(y) > 1) and len(x)>min_windows:
            bad_clusters.append(i)
    
    return bad_clusters

def remove_furthest(df, cluster):
    if cluster is None:
        dff = df
    else:
        dff = df[df['labels'] == cluster]
    
    centroid = np.array([dff['dCpskew'].mean(), dff['dCpkurt'].mean()])
    distances = np.sqrt((dff['dCpskew'] - centroid[0])**2 + (dff['dCpkurt'] - centroid[1])**2)
    df.drop(distances.idxmax(), inplace=True)

def recluster(df, indices, min_windows):
    print('Reclustering...')
    for i in indices:
        dff = df[df['labels'] == i]
        print('Cluster %d has %d points' %(i, dff.shape[0]))
        N = int(np.floor(dff.shape[0] / min_windows))
        X = np.column_stack((dff['dCpskew'], dff['dCpkurt']))
        model = KMeans(n_clusters=N).fit(X)

        df.loc[df['labels'] == i, 'labels'] = dff['labels'] + model.labels_
        return df

def calculate_dCpmin(position, df, df_agg, min_windows):
    # First check if need to recluster:
    # totals = df['labels'].value_counts()
    # if (totals > 32).any():
    #     # One or more of the clusters has >32 samples, recluster this/these set
    #     df = recluster(df, totals[totals >= 32].index.values)

    # plt.scatter(df['dCpskew'], df['dCpkurt'], c=df['labels'], cmap='viridis', s=50, alpha=0.7)
    # plt.title('Position: %.2f' %(pos))
    # plt.colorbar()
    # plt.show()

    for i in np.unique(df['labels']):
        dff = df[df['labels'] == i]
        # If it is a valid group, calculate overall dCpmin using Cook and Mayne method
        if (np.ptp(dff['dCpskew']) < 1 and np.ptp(dff['dCpkurt']) < 1) and dff.shape[0] >= min_windows:
            dCpmin = common.gumbel_min_6(dff['dCpmin_noEV'])
            df_new = pd.DataFrame(
                {'Position':[position], 
                 'N_windows':[dff.shape[0]],
                 'dCprms_avg':[dff['dCprms'].mean()], 
                 'dCprms_range':[np.ptp(dff['dCprms'])], 
                 'dCpmin_160':[dCpmin],
                 'dCpskew_range':[np.ptp(dff['dCpskew'])],
                 'dCpkurt_range':[np.ptp(dff['dCpkurt'])]})
            df_agg = pd.concat([df_agg, df_new], ignore_index=True)

    return df_agg

def perform_aggregation(df, Iu_bounds, min_windows, method):
    # Import range by position data:
    ranges = pd.read_csv('Cp_skew_kurt_ranges.csv')

    # Filter by turbulence intensity:
    df = df[np.logical_and(df['TurbIntensity_x'] > Iu_bounds[0], df['TurbIntensity_x'] < Iu_bounds[1])]

    # Get positions, will cluster for each position:
    positions = df['Position'].unique()
    positions.sort()

    # Initialize dCpmin aggregated dataset:
    df_agg = pd.DataFrame(columns=['Position', 'N_windows', 'dCprms_avg', 'dCprms_range', 'dCpmin_160', 'dCpskew_range', 'dCpkurt_range'])

    for pos in positions: #[75.72]: 
        dff = df[df['Position'] == pos]
        N_points = dff.shape[0]
        print('------------- Position %.2f, Ntot = %d -------------' %(pos, N_points))

        # Normalize dCpskew and dCpkurt by max range so they are weighted equally:
        if method == 'MC_ranges':
            dCpskew_range, dCpkurt_range = MC_ranges(np.mean(dff['dCprms']), stats.mode(np.sign(dff['dCpskew'])), 7500)
        elif method == 'LES_ranges':
            dCpskew_range, dCpkurt_range = ranges_by_position(ranges, pos)
            dff['dCpskew'] = dff['dCpskew'] / dCpskew_range
            dff['dCpkurt'] = dff['dCpkurt'] / dCpkurt_range
        else:
            raise ValueError('method must be "MC_ranges" for monte-carlo sampling derived skewness and kurtosis ranges or "LES_ranges" for LES-derived ranges')

        if N_points >= min_windows:
            determine_clusters(dff)
            # N_clusters = int(np.floor(N_points / min_windows))
            # determine_clusters_alt(dff, N_clusters)

            # plt.scatter(dff['dCpskew'], dff['dCpkurt'], c=dff['labels'], cmap='viridis', s=50, alpha=0.7)
            # plt.title('Position: %.2f' %(pos))
            # # plt.colorbar()
            # plt.show()

            # Check clusters are not too sparse, if a cluster is too sparse, 
            # drop its furthest datapoint and re-perform clustering
            bad_clusters = check_clusters(dff, min_windows)
            while len(bad_clusters) != 0:
                # remove the furthest datapoint in the offending cluster
                for i in bad_clusters:
                    # print('Removing furthest datapoint in cluster ' + str(i))
                    remove_furthest(dff, i)
                bad_clusters = check_clusters(dff, min_windows)

            # Calculate dCpmin and add it to the aggregated dataset
            df_agg = calculate_dCpmin(pos, dff, df_agg, min_windows)
    
    return df_agg

def plot_agg_meas_points(fig, df, stats, color):
    agg_stats = stats.copy()

    # Only setup for tethered motes (parapet side)
    for row in range(len(stats)): 
        if agg_stats[row] == 'dCprms':
            agg_stats[row] = 'dCprms_avg'
        elif agg_stats[row] == 'dCpmin':
            agg_stats[row] = 'dCpmin_160'
        else:
            continue

        fig.add_trace(go.Scatter(
            x=df['Position'],
            y=df[agg_stats[row]],
            mode='markers',
            marker_color=df[color],
            marker = dict(
                symbol='diamond',
                size=10,
                line=dict(
                    width=1,
                    color='white'
                ),
                colorscale='Haline', 
                opacity=1
            ),
            showlegend=False),
        row=row+1, col=1)