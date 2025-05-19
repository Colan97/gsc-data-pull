import streamlit as st
import pandas as pd
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import tempfile
import os
import json
from datetime import datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go

# Enable debug mode
st.set_option('client.showErrorDetails', True)

# Page config
st.set_page_config(
    page_title="GSC Data Explorer",
    page_icon="📊",
    layout="wide"
)

# Constants
SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']
REDIRECT_URI = "https://gsc-data-extraction.streamlit.app/"

def get_credentials():
    if 'credentials' not in st.session_state:
        return None
    return Credentials(**st.session_state.credentials)

def get_all_data(service, site_url, request_body):
    all_rows = []
    start_row = 0
    batch_size = 25000  # Maximum allowed by the API
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    while True:
        request_body['startRow'] = start_row
        request_body['rowLimit'] = batch_size
        
        response = service.searchanalytics().query(
            siteUrl=site_url,
            body=request_body
        ).execute()
        
        rows = response.get('rows', [])
        if not rows:
            break
            
        all_rows.extend(rows)
        start_row += len(rows)
        
        # Update progress
        progress = min(1.0, len(all_rows) / 100000)  # Assuming max 100k rows
        progress_bar.progress(progress)
        status_text.text(f"Downloaded {len(all_rows)} rows...")
        
        # If we got fewer rows than requested, we've reached the end
        if len(rows) < batch_size:
            break
    
    progress_bar.empty()
    status_text.empty()
    return all_rows

def main():
    try:
        st.title("Google Search Console Data Explorer")
        
        # Debug message
        st.write("Debug: Starting main function")
        
        # Sidebar for authentication
        with st.sidebar:
            st.header("Authentication")
            if 'credentials' not in st.session_state:
                if st.button("Sign in with Google"):
                    try:
                        # Debug message
                        st.write("Debug: Attempting to get secrets")
                        
                        # Get credentials from Streamlit secrets
                        secrets = st.secrets["web"]
                        
                        # Debug message
                        st.write("Debug: Got secrets, creating flow")
                        
                        # Create flow from secrets
                        flow = Flow.from_client_config(
                            {
                                "web": {
                                    "client_id": secrets["client_id"],
                                    "project_id": secrets["project_id"],
                                    "auth_uri": secrets["auth_uri"],
                                    "token_uri": secrets["token_uri"],
                                    "auth_provider_x509_cert_url": secrets["auth_provider_x509_cert_url"],
                                    "client_secret": secrets["client_secret"],
                                    "redirect_uris": secrets["redirect_uris"],
                                    "javascript_origins": secrets["javascript_origins"]
                                }
                            },
                            scopes=SCOPES,
                            redirect_uri=REDIRECT_URI
                        )
                        
                        # Debug message
                        st.write("Debug: Created flow, getting auth URL")
                        
                        auth_url, _ = flow.authorization_url(
                            access_type='offline',
                            include_granted_scopes='true'
                        )
                        st.markdown(f"[Click here to authorize]({auth_url})")
                        
                        # Get the authorization code from URL parameters
                        query_params = st.experimental_get_query_params()
                        if 'code' in query_params:
                            auth_code = query_params['code'][0]
                            try:
                                # Debug message
                                st.write("Debug: Got auth code, fetching token")
                                
                                flow.fetch_token(code=auth_code)
                                credentials = flow.credentials
                                st.session_state.credentials = {
                                    'token': credentials.token,
                                    'refresh_token': credentials.refresh_token,
                                    'token_uri': credentials.token_uri,
                                    'client_id': credentials.client_id,
                                    'client_secret': credentials.client_secret,
                                    'scopes': credentials.scopes
                                }
                                st.experimental_rerun()
                            except Exception as e:
                                st.error(f"Authentication failed: {str(e)}")
                                st.write(f"Debug: Error details: {str(e)}")
                    except Exception as e:
                        st.error(f"Error during authentication setup: {str(e)}")
                        st.write(f"Debug: Error details: {str(e)}")
            else:
                st.success("✅ Authenticated")
                if st.button("Sign Out"):
                    del st.session_state.credentials
                    st.experimental_rerun()

        # Main content
        if 'credentials' in st.session_state:
            try:
                # Debug message
                st.write("Debug: Getting credentials")
                
                credentials = get_credentials()
                service = build('searchconsole', 'v1', credentials=credentials)
                
                # Debug message
                st.write("Debug: Getting sites")
                
                # Get available sites
                sites = service.sites().list().execute()
                site_entries = sites.get('siteEntry', [])
                
                if not site_entries:
                    st.warning("No sites found in your Google Search Console account.")
                    return
                
                # Site selection
                site_url = st.selectbox(
                    "Select your site:",
                    options=[site['siteUrl'] for site in site_entries]
                )
                
                # Data type selection
                data_type = st.radio(
                    "Select data type:",
                    options=["Site-level Data", "URL-level Data"],
                    horizontal=True
                )
                data_type = "url" if data_type == "URL-level Data" else "site"
                
                # Date range selection
                col1, col2 = st.columns(2)
                with col1:
                    start_date = st.date_input(
                        "Start Date",
                        value=datetime.now() - timedelta(days=30)
                    )
                with col2:
                    end_date = st.date_input(
                        "End Date",
                        value=datetime.now()
                    )
                
                if st.button("Fetch Data"):
                    with st.spinner("Fetching data..."):
                        request_body = {
                            'startDate': start_date.strftime('%Y-%m-%d'),
                            'endDate': end_date.strftime('%Y-%m-%d'),
                            'dimensions': ['query', 'page', 'device', 'country'] if data_type == 'url' else ['query', 'device', 'country'],
                        }
                        
                        all_rows = get_all_data(service, site_url, request_body)
                        
                        if not all_rows:
                            st.warning("No data found for the selected parameters.")
                            return
                        
                        # Convert to DataFrame
                        data = []
                        for row in all_rows:
                            row_data = {
                                'clicks': row['clicks'],
                                'impressions': row['impressions'],
                                'ctr': row['ctr'],
                                'position': row['position'],
                                'query': row['keys'][0],
                                'device': row['keys'][1],
                                'country': row['keys'][2]
                            }
                            if data_type == 'url':
                                row_data['page'] = row['keys'][3]
                            data.append(row_data)
                        
                        df = pd.DataFrame(data)
                        
                        # Store in session state
                        st.session_state.df = df
                        
                        # Display summary
                        st.success(f"✅ Downloaded {len(df)} rows of data")
                        
                        # Show data preview
                        st.subheader("Data Preview")
                        st.dataframe(df.head())
                        
                        # Download button
                        csv = df.to_csv(index=False)
                        st.download_button(
                            label="Download CSV",
                            data=csv,
                            file_name=f"gsc_data_{data_type}_{start_date}_{end_date}.csv",
                            mime="text/csv"
                        )
                        
                        # Visualizations
                        st.subheader("Data Visualizations")
                        
                        # Top queries by clicks
                        fig_clicks = px.bar(
                            df.groupby('query')['clicks'].sum().sort_values(ascending=False).head(10).reset_index(),
                            x='query',
                            y='clicks',
                            title='Top 10 Queries by Clicks'
                        )
                        st.plotly_chart(fig_clicks, use_container_width=True)
                        
                        # Device distribution
                        fig_device = px.pie(
                            df.groupby('device')['clicks'].sum().reset_index(),
                            values='clicks',
                            names='device',
                            title='Clicks by Device'
                        )
                        st.plotly_chart(fig_device, use_container_width=True)
                        
                        # Position trend
                        fig_position = px.line(
                            df.groupby('query')['position'].mean().sort_values().reset_index(),
                            x='query',
                            y='position',
                            title='Average Position by Query'
                        )
                        st.plotly_chart(fig_position, use_container_width=True)
                
            except Exception as e:
                st.error(f"Error in main content: {str(e)}")
                st.write(f"Debug: Error details: {str(e)}")
                
    except Exception as e:
        st.error(f"Critical error: {str(e)}")
        st.write(f"Debug: Error details: {str(e)}")

if __name__ == "__main__":
    main() 
