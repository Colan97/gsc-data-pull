import streamlit as st

# Check for required dependencies and show helpful error messages
try:
    from google_auth_oauthlib.flow import Flow
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    import google.auth.exceptions
    import google.auth.transport.requests
except ImportError as e:
    st.error(f"""
    Missing required Google API dependencies. Please ensure you have a requirements.txt file with:
    
    ```
    streamlit>=1.45.1
    google-auth-oauthlib>=1.0.0
    google-auth>=2.0.0
    google-api-python-client>=2.0.0
    pandas>=2.2.3
    python-dateutil>=2.9.0
    ```
    
    Error: {str(e)}
    """)
    st.stop()

try:
    import pandas as pd
    from datetime import datetime, timedelta
    from dateutil.relativedelta import relativedelta
    import json
    import os
except ImportError as e:
    st.error(f"Missing required dependencies: {str(e)}")
    st.stop()

# --- Configuration ---
def check_secrets():
    """Check if all required secrets are configured"""
    required_secrets = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "REDIRECT_URI"]
    missing_secrets = []
    
    for secret in required_secrets:
        if secret not in st.secrets:
            missing_secrets.append(secret)
    
    if missing_secrets:
        st.error(f"""
        Missing OAuth credentials in st.secrets: {', '.join(missing_secrets)}
        
        Please configure them in .streamlit/secrets.toml:
        
        ```toml
        GOOGLE_CLIENT_ID = "your_client_id"
        GOOGLE_CLIENT_SECRET = "your_client_secret"
        REDIRECT_URI = "your_redirect_uri"
        ```
        """)
        return False
    return True

if not check_secrets():
    st.stop()

try:
    GOOGLE_CLIENT_ID = st.secrets["GOOGLE_CLIENT_ID"]
    GOOGLE_CLIENT_SECRET = st.secrets["GOOGLE_CLIENT_SECRET"]
    REDIRECT_URI = st.secrets["REDIRECT_URI"]

    CLIENT_CONFIG = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "project_id": "", # Optional: You can add your GCP project ID here
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uris": [REDIRECT_URI],
        }
    }
except Exception as e:
    st.error(f"Error configuring OAuth credentials: {str(e)}")
    st.stop()

SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']
API_SERVICE_NAME = 'searchconsole'
API_VERSION = 'v1'

# --- Authentication Functions ---
def get_flow():
    """Create OAuth flow object"""
    try:
        return Flow.from_client_config(
            client_config=CLIENT_CONFIG,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )
    except Exception as e:
        st.error(f"Error creating OAuth flow: {str(e)}")
        return None

def get_credentials_from_session():
    """Retrieve and validate credentials from session state"""
    if 'credentials' not in st.session_state:
        return None
    try:
        creds_dict = json.loads(st.session_state['credentials'])
        credentials = Credentials(**creds_dict)
        if credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(google.auth.transport.requests.Request())
                st.session_state['credentials'] = credentials.to_json() # Update session with refreshed token
            except google.auth.exceptions.RefreshError as e:
                st.error(f"Error refreshing token: {e}. Please login again.")
                del st.session_state['credentials']
                st.session_state.pop('user_info', None)
                return None
        return credentials
    except Exception as e:
        st.error(f"Error loading credentials: {e}")
        if 'credentials' in st.session_state:
            del st.session_state['credentials']
        st.session_state.pop('user_info', None)
        return None

def login_clicked():
    """Handle login button click"""
    flow = get_flow()
    if not flow:
        return
    
    try:
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent' # Force consent screen for refresh token
        )
        st.session_state['oauth_state'] = state
        # Redirect to authorization_url
        st.markdown(f'<meta http-equiv="refresh" content="0;url={authorization_url}">', unsafe_allow_html=True)
        st.markdown(f'Please [click here to authorize]({authorization_url}) if you are not redirected automatically.')
    except Exception as e:
        st.error(f"Error initiating login: {str(e)}")

# --- GSC API Functions ---
def list_sites(credentials):
    """List all sites accessible in Google Search Console"""
    if not credentials:
        return []
    try:
        service = build(API_SERVICE_NAME, API_VERSION, credentials=credentials)
        site_list = service.sites().list().execute()
        return [s['siteUrl'] for s in site_list.get('siteEntry', []) if s.get('permissionLevel') != 'siteUnverifiedUser']
    except Exception as e:
        st.error(f"Error listing sites: {e}")
        if "invalid_grant" in str(e).lower() or "token has been revoked" in str(e).lower():
             if 'credentials' in st.session_state: 
                 del st.session_state['credentials']
             st.warning("Authentication error. Please try logging in again.")
             st.rerun()
        return []

def fetch_gsc_data(credentials, site_url, start_date_str, end_date_str, dimensions, search_type='web', row_limit=25000):
    """Fetch data from Google Search Console API"""
    if not credentials:
        st.error("Not authenticated. Cannot fetch data.")
        return []
    
    try:
        service = build(API_SERVICE_NAME, API_VERSION, credentials=credentials)
        request_body = {
            'startDate': start_date_str,
            'endDate': end_date_str,
            'dimensions': dimensions,
            'type': search_type,
            'rowLimit': row_limit,
            'dataState': 'all' # Includes fresh data too
        }
        response = service.searchanalytics().query(siteUrl=site_url, body=request_body).execute()
        return response.get('rows', [])
    except Exception as e:
        st.error(f"Error fetching GSC data for {site_url} ({start_date_str} to {end_date_str}): {e}")
        if "invalid_grant" in str(e).lower() or "token has been revoked" in str(e).lower():
             if 'credentials' in st.session_state: 
                 del st.session_state['credentials']
             st.warning("Authentication error during data fetch. Please try logging in again.")
             st.rerun()
        return []

# --- Data Processing Functions ---
def process_gsc_api_response(api_data, requested_dimensions):
    """Process raw GSC API response into a structured DataFrame"""
    if not api_data:
        return pd.DataFrame()

    try:
        df = pd.DataFrame(api_data)

        # Ensure essential metric columns exist, GSC API might not return them if all values are zero
        for metric_col in ['clicks', 'impressions', 'ctr', 'position']:
            if metric_col not in df.columns:
                df[metric_col] = 0.0 # Initialize with float
        
        # Extract dimensions from 'keys'
        if 'keys' in df.columns:
            for i, dim_name in enumerate(requested_dimensions):
                try:
                    df[dim_name] = df['keys'].apply(lambda x: x[i] if len(x) > i else None)
                except (IndexError, TypeError):
                    st.warning(f"Could not extract dimension '{dim_name}' from 'keys'.")
                    df[dim_name] = None
        else: # If 'keys' is not there, dimensions might already be columns (should not happen with searchAnalytics)
            for dim_name in requested_dimensions:
                if dim_name not in df.columns:
                    st.warning(f"Expected dimension '{dim_name}' not found in GSC response.")
                    df[dim_name] = None

        if 'date' not in df.columns:
            st.error("Critical: 'date' dimension could not be processed from GSC response.")
            return pd.DataFrame(columns=['clicks', 'impressions', 'ctr', 'position'])

        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df.dropna(subset=['date'], inplace=True) # Remove rows where date conversion failed
        if df.empty:
            st.warning("No valid date entries found after processing GSC response.")
            return pd.DataFrame()

        df['month_year'] = df['date'].dt.to_period('M')
        
        # Convert metrics to numeric, coercing errors
        for col in ['clicks', 'impressions', 'position', 'ctr']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # API's CTR is a fraction (0 to 1), convert to percentage
        if 'ctr' in df.columns:
            df['ctr'] = df['ctr'] * 100

        return df
    except Exception as e:
        st.error(f"Error processing GSC API response: {str(e)}")
        return pd.DataFrame()

def calculate_mom_metrics(df_current_period_processed, df_previous_period_processed, report_level):
    """Calculate month-over-month metrics"""
    try:
        agg_config = {'clicks': 'sum', 'impressions': 'sum', 'position': 'mean'}
        group_by_cols = ['month_year']
        if report_level == "page-level" and 'page' in df_current_period_processed.columns:
            group_by_cols.append('page')

        def aggregate_monthly_data(df, grouping_cols):
            if df.empty or not all(col in df.columns for col in grouping_cols):
                # Define columns for an empty aggregated DataFrame
                return pd.DataFrame(columns=grouping_cols + list(agg_config.keys()) + ['ctr'])

            # Ensure all metric columns are numeric before aggregation
            for metric_col in agg_config.keys():
                if metric_col in df.columns:
                    df[metric_col] = pd.to_numeric(df[metric_col], errors='coerce')
                else: # If a metric column is missing, add it with NaN
                    df[metric_col] = float('nan')
            
            # Perform aggregation
            grouped_df = df.groupby(grouping_cols, as_index=False).agg(agg_config)
            
            # Recalculate CTR
            if 'clicks' in grouped_df.columns and 'impressions' in grouped_df.columns:
                grouped_df['ctr'] = (grouped_df['clicks'] / grouped_df['impressions'].replace(0, float('nan'))) * 100
                grouped_df['ctr'] = grouped_df['ctr'].fillna(0)
            else:
                grouped_df['ctr'] = float('nan')
            return grouped_df

        current_agg = aggregate_monthly_data(df_current_period_processed, group_by_cols)
        previous_agg_raw = aggregate_monthly_data(df_previous_period_processed, group_by_cols)
        
        # Prepare previous_agg for merging
        if not previous_agg_raw.empty:
            previous_agg = previous_agg_raw.rename(columns={
                **{k: f'prev_{k}' for k in agg_config.keys()},
                'ctr': 'prev_ctr'
            })
            # Shift month_year to align for MoM calculation (previous month's data is for current month's comparison)
            previous_agg['month_year'] = previous_agg['month_year'].apply(lambda p: p + 1 if pd.notnull(p) else pd.NaT)
        else: # Create empty df with correct columns if previous data is missing
            prev_cols_renamed = [f'prev_{m}' for m in agg_config.keys()] + ['prev_ctr']
            previous_agg = pd.DataFrame(columns=group_by_cols + prev_cols_renamed)

        if current_agg.empty:
            return pd.DataFrame() # No current data, so no MoM

        # Merge current and previous period aggregated data
        merged_df = pd.merge(current_agg, previous_agg, on=group_by_cols, how='left')

        # Calculate MoM percentages
        for metric in list(agg_config.keys()) + ['ctr']:
            current_col = metric
            prev_col = f'prev_{metric}'
            mom_col = f'{metric}_mom'

            if current_col in merged_df.columns and prev_col in merged_df.columns:
                # MoM = ((Current - Previous) / Previous) * 100
                # FillNa(0) for current and previous values in calculation to avoid errors with missing data
                # but use .replace(0, float('nan')) for denominator to avoid division by zero errors if previous was truly 0.
                numerator = merged_df[current_col].fillna(0) - merged_df[prev_col].fillna(0)
                denominator = merged_df[prev_col].fillna(0).replace(0, float('nan'))
                
                if metric == 'position': # For position, improvement is a decrease
                    # MoM % change for position: (Prev - Curr) / Prev * 100
                    # So a positive % means improvement (lower position number)
                    numerator = merged_df[prev_col].fillna(float('inf')) - merged_df[current_col].fillna(float('inf'))
                    denominator = merged_df[prev_col].fillna(float('inf')).replace(float('inf'), float('nan')) # Avoid div by inf if prev was inf

                merged_df[mom_col] = (numerator / denominator) * 100
                
                # Handle cases:
                # 1. Previous is 0 or NaN, Current > 0: MoM is effectively infinite positive. Show as 100% or "New".
                #    (Current logic with replace(0, nan) in denominator results in NaN here for prev 0, then fillna(0) below)
                # 2. Current is 0, Previous > 0: MoM is -100%.
                # 3. Both 0 or NaN: MoM is 0.
                merged_df.loc[merged_df[prev_col].fillna(0) == 0 & (merged_df[current_col].fillna(0) > 0) & (metric != 'position'), mom_col] = 100.0 # Mark as 100% if new
                merged_df.loc[merged_df[prev_col].fillna(float('inf')) == float('inf') & (merged_df[current_col].fillna(float('inf')) < float('inf')) & (metric == 'position'), mom_col] = 100.0 # Mark as 100% improvement if new

                merged_df[mom_col] = merged_df[mom_col].fillna(0) # Default NaN MoM to 0
            else:
                merged_df[mom_col] = 0.0 # If columns are missing

        return merged_df
    except Exception as e:
        st.error(f"Error calculating MoM metrics: {str(e)}")
        return pd.DataFrame()

# --- Streamlit App UI ---
st.set_page_config(layout="wide")
st.title("📈 Google Search Console MoM Data Extractor")

# Handle OAuth Callback
query_params = st.query_params
if 'code' in query_params and 'state' in query_params:
    if 'oauth_state' not in st.session_state or st.session_state['oauth_state'] != query_params.get('state'):
        st.error("OAuth state mismatch. Please try logging in again.")
        # Attempt to clear query params by rerunning without them
        st.query_params.clear()
    else:
        try:
            flow = get_flow()
            if flow:
                flow.fetch_token(code=query_params.get('code'))
                credentials_obj = flow.credentials
                st.session_state['credentials'] = credentials_obj.to_json()
                st.session_state.pop('oauth_state', None) # Clean up state
                st.success("🔑 Successfully authenticated!")
                # Clear query params and rerun
                st.query_params.clear()
                st.rerun()
        except Exception as e:
            st.error(f"Error fetching token: {e}")
            # Attempt to clear query params
            st.query_params.clear()

credentials = get_credentials_from_session()

if not credentials:
    st.sidebar.button("🔒 Login with Google", on_click=login_clicked)
    st.info("Please login using the button in the sidebar to fetch GSC data.")
    st.stop()

# --- Logged In State ---
st.sidebar.success("✅ Logged In")
if st.sidebar.button("🚪 Logout"):
    if 'credentials' in st.session_state: 
        del st.session_state['credentials']
    st.query_params.clear() # Clear any OAuth codes from URL on logout
    st.rerun()

sites = list_sites(credentials)
if not sites:
    st.warning("No sites found or accessible for your GSC account, or there was an issue fetching them.")
    st.stop()

st.sidebar.markdown("---")
selected_site = st.sidebar.selectbox("💻 Select Your Website:", sites, help="Choose the GSC property to analyze.")

report_level = st.sidebar.selectbox("📊 Select Report Level:", ["site-level", "page-level"], help="Site-level aggregates data for the entire site. Page-level provides data for each page.")

# Date Range Selection - GSC data has a lag of about 2-3 days
# We want to select full months for MoM comparison.
st.sidebar.markdown("🗓️ **Select Analysis Period End Month**")
today = datetime.today()
# Default to end of last month
default_report_month = (today - relativedelta(months=1)).replace(day=1)

report_month_selected = st.sidebar.date_input(
    "Month to Report On (MoM vs Previous)",
    value=default_report_month,
    min_value=datetime(2018,1,1), # GSC API historical limit
    max_value=today.replace(day=1) - relativedelta(days=1), # Max is end of prior month
    format="YYYY-MM",
    help="Select any day in the month you want to analyze. The report will cover this full month vs the prior full month."
)

# Determine the number of past months for MoM trend
num_months_trend = st.sidebar.slider(
    "Number of Past Months for Trend (incl. selected):", 
    min_value=1, max_value=12, value=3,
    help="How many months of MoM data to show, ending with your selected month."
)

st.sidebar.markdown("---")

if st.sidebar.button("🚀 Fetch & Analyze MoM Data", type="primary"):
    if not selected_site:
        st.warning("Please select a site.")
        st.stop()

    st.header(f"MoM Analysis for: {selected_site}", divider="rainbow")
    st.subheader(f"Report Level: {report_level.replace('-', ' ').capitalize()}")

    all_mom_dataframes = []
    
    # Iterate backwards from the selected report_month_selected for the trend
    for i in range(num_months_trend):
        current_month_end_date = (datetime(report_month_selected.year, report_month_selected.month, 1) - relativedelta(months=i) + relativedelta(months=1) - timedelta(days=1))
        current_month_start_date = current_month_end_date.replace(day=1)

        previous_month_end_date = current_month_start_date - timedelta(days=1)
        previous_month_start_date = previous_month_end_date.replace(day=1)

        # GSC API limits queries to start dates no more than 16 months ago (roughly)
        # And data is available after 2-3 days delay.
        # Ensure dates are not too far in the past or future.
        sixteen_months_ago = today - relativedelta(months=16)
        three_days_ago = today - timedelta(days=3)

        if current_month_start_date < sixteen_months_ago.replace(day=1) or \
           previous_month_start_date < sixteen_months_ago.replace(day=1):
            st.warning(f"Skipping MoM for {current_month_start_date.strftime('%B %Y')} as it's beyond the ~16-month GSC API limit.")
            continue
        if current_month_end_date > three_days_ago or previous_month_end_date > three_days_ago:
             st.warning(f"Skipping MoM for {current_month_start_date.strftime('%B %Y')} as data may not be fully available yet (requires ~3 day lag).")
             continue

        st.markdown(f"--- \n#### 🔎 Analyzing: {current_month_start_date.strftime('%B %Y')} vs {previous_month_start_date.strftime('%B %Y')}")

        gsc_dimensions_for_query = ['date'] # Always need date for monthly aggregation
        if report_level == "page-level":
            gsc_dimensions_for_query.append('page')

        # Fetch data for current month
        with st.spinner(f"Fetching data for {current_month_start_date.strftime('%Y-%m-%d')} to {current_month_end_date.strftime('%Y-%m-%d')}..."):
            raw_data_current = fetch_gsc_data(credentials, selected_site,
                                                 current_month_start_date.strftime('%Y-%m-%d'),
                                                 current_month_end_date.strftime('%Y-%m-%d'),
                                                 gsc_dimensions_for_query)
        df_current_processed = process_gsc_api_response(raw_data_current, gsc_dimensions_for_query)

        # Fetch data for previous month
        with st.spinner(f"Fetching data for {previous_month_start_date.strftime('%Y-%m-%d')} to {previous_month_end_date.strftime('%Y-%m-%d')}..."):
            raw_data_previous = fetch_gsc_data(credentials, selected_site,
                                               previous_month_start_date.strftime('%Y-%m-%d'),
                                               previous_month_end_date.strftime('%Y-%m-%d'),
                                               gsc_dimensions_for_query)
        df_previous_processed = process_gsc_api_response(raw_data_previous, gsc_dimensions_for_query)
        
        if df_current_processed.empty and df_previous_processed.empty:
            st.write(f"No data found for the period {current_month_start_date.strftime('%B %Y')} or {previous_month_start_date.strftime('%B %Y')}.")
        else:
            mom_df_for_period = calculate_mom_metrics(df_current_processed, df_previous_processed, report_level)

            if not mom_df_for_period.empty:
                display_cols_order = ['month_year']
                if report_level == "page-level" and 'page' in mom_df_for_period.columns:
                    display_cols_order.append('page')
                
                metrics_for_display = ['clicks', 'impressions', 'ctr', 'position']
                for metric_name in metrics_for_display:
                    if metric_name in mom_df_for_period.columns: 
                        display_cols_order.append(metric_name)
                    if f'prev_{metric_name}' in mom_df_for_period.columns: 
                        display_cols_order.append(f'prev_{metric_name}')
                    if f'{metric_name}_mom' in mom_df_for_period.columns: 
                        display_cols_order.append(f'{metric_name}_mom')
                
                # Filter to existing columns before display
                mom_df_for_period_display = mom_df_for_period[[col for col in display_cols_order if col in mom_df_for_period.columns]]

                st.dataframe(mom_df_for_period_display.style.format({
                    'ctr': "{:.2f}%", 'prev_ctr': "{:.2f}%", 'ctr_mom': "{:.1f}%",
                    'position': "{:.2f}", 'prev_position': "{:.2f}", 'position_mom': "{:.1f}%",
                    'clicks_mom': "{:.1f}%", 'impressions_mom': "{:.1f}%",
                    'clicks': "{:,.0f}", 'prev_clicks': "{:,.0f}",
                    'impressions': "{:,.0f}", 'prev_impressions': "{:,.0f}"
                }), use_container_width=True)
                all_mom_dataframes.append(mom_df_for_period_display)
            else:
                st.write(f"Could not compute MoM data for {current_month_start_date.strftime('%B %Y')}.")
    
    if all_mom_dataframes:
        try:
            final_report_df = pd.concat(all_mom_dataframes).sort_values(
                by=['month_year'] + (['page'] if report_level == 'page-level' and 'page' in pd.concat(all_mom_dataframes).columns else []), 
                ascending=[False] + ([True] if report_level == 'page-level' and 'page' in pd.concat(all_mom_dataframes).columns else [])
            ).reset_index(drop=True)
            
            st.markdown("--- \n### 💾 Combined MoM Data (Downloadable)")
            csv = final_report_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Full Report as CSV",
                data=csv,
                file_name=f"{selected_site.replace('://', '_').replace('/', '_')}_mom_report_{report_month_selected.strftime('%Y-%m')}_{num_months_trend}months.csv",
                mime='text/csv',
            )
        except Exception as e:
            st.error(f"Error processing final report: {str(e)}")
    else:
        st.info("No MoM data generated for the selected period and trend length.")
