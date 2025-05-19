import streamlit as st
import pandas as pd
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os
import json
from datetime import datetime, timedelta
import plotly.express as px
import tempfile

# Page config
st.set_page_config(
    page_title="GSC Data Explorer",
    page_icon="📊",
    layout="wide"
)

# Constants
SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']
CLIENT_SECRETS_FILE = "client_secrets.json"
REDIRECT_URI = "https://need-gsc-data.streamlit.app/"

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
    st.title("Google Search Console Data Explorer")
    
    # Initialize session state
    if 'credentials' not in st.session_state:
        st.session_state.credentials = None
    
    # Sidebar for authentication
    with st.sidebar:
        st.header("Authentication")
        if st.session_state.credentials is None:
            if st.button("Sign in with Google"):
                flow = Flow.from_client_secrets_file(
                    CLIENT_SECRETS_FILE,
                    scopes=SCOPES,
                    redirect_uri=REDIRECT_URI
                )
                auth_url, _ = flow.authorization_url(
                    access_type='offline',
                    include_granted_scopes='true'
                )
                st.markdown(f"[Click here to authorize]({auth_url})")
                
                auth_code = st.text_input("Enter the authorization code:")
                if auth_code:
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
        else:
            st.success("✅ Authenticated")
            if st.button("Sign Out"):
                st.session_state.credentials = None
                st.experimental_rerun()
    
    # Main content
    if st.session_state.credentials:
        credentials = get_credentials()
        service = build('searchconsole', 'v1', credentials=credentials)
        
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
        
        if st.button("Download Data"):
            with st.spinner("Downloading data..."):
                # Prepare the request
                request_body = {
                    'startDate': start_date.strftime('%Y-%m-%d'),
                    'endDate': end_date.strftime('%Y-%m-%d'),
                    'dimensions': ['query', 'page', 'device', 'country'] if data_type == "URL-level Data" else ['query', 'device', 'country'],
                }
                
                # Get all data
                all_rows = get_all_data(service, site_url, request_body)
                
                if not all_rows:
                    st.warning("No data found for the selected period.")
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
                    if data_type == "URL-level Data":
                        row_data['page'] = row['keys'][3]
                    data.append(row_data)
                
                df = pd.DataFrame(data)
                
                # Display summary
                st.subheader("Data Summary")
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total Clicks", f"{df['clicks'].sum():,}")
                with col2:
                    st.metric("Total Impressions", f"{df['impressions'].sum():,}")
                with col3:
                    st.metric("Average CTR", f"{df['ctr'].mean():.2%}")
                with col4:
                    st.metric("Average Position", f"{df['position'].mean():.2f}")
                
                # Visualizations
                st.subheader("Data Visualizations")
                tab1, tab2, tab3 = st.tabs(["Top Queries", "Device Distribution", "Country Distribution"])
                
                with tab1:
                    top_queries = df.groupby('query').agg({
                        'clicks': 'sum',
                        'impressions': 'sum'
                    }).sort_values('clicks', ascending=False).head(10)
                    
                    fig = px.bar(
                        top_queries,
                        y=top_queries.index,
                        x='clicks',
                        orientation='h',
                        title="Top 10 Queries by Clicks"
                    )
                    st.plotly_chart(fig, use_container_width=True)
                
                with tab2:
                    device_dist = df.groupby('device').agg({
                        'clicks': 'sum',
                        'impressions': 'sum'
                    })
                    
                    fig = px.pie(
                        device_dist,
                        values='clicks',
                        names=device_dist.index,
                        title="Clicks by Device"
                    )
                    st.plotly_chart(fig, use_container_width=True)
                
                with tab3:
                    country_dist = df.groupby('country').agg({
                        'clicks': 'sum'
                    }).sort_values('clicks', ascending=False).head(10)
                    
                    fig = px.bar(
                        country_dist,
                        x=country_dist.index,
                        y='clicks',
                        title="Top 10 Countries by Clicks"
                    )
                    st.plotly_chart(fig, use_container_width=True)
                
                # Download button
                csv = df.to_csv(index=False)
                st.download_button(
                    label="Download CSV",
                    data=csv,
                    file_name=f"gsc_data_{data_type.replace(' ', '_')}_{start_date}_{end_date}.csv",
                    mime="text/csv"
                )
                
                # Data preview
                st.subheader("Data Preview")
                st.dataframe(df.head(1000))
    else:
        st.info("Please sign in with your Google account to access the data.")

if __name__ == "__main__":
    main() 
