import os
import sys
import logging
import argparse
from pynetdicom import AE, evt, StoragePresentationContexts

# Import our shared utilities : common.py
from common import safe_dicom_send, is_within_schedule, get_conf_val, get_config

# --- GLOBAL LOGGING INITIALIZATION ---
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
formatter = logging.Formatter(LOG_FORMAT)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)

logger = logging.getLogger("Router")
logger.addHandler(console_handler)
logger.propagate = False 

def refresh_logger(log_obj, name, level_str):
    """Updates logger identity and synchronizes pynetdicom logging."""
    level = getattr(logging, level_str.upper(), logging.INFO)
    
    log_obj.name = name
    log_obj.setLevel(level)
    console_handler.setLevel(level)

    pynet_logger = logging.getLogger('pynetdicom')
    pynet_logger.setLevel(level)
    
    for h in pynet_logger.handlers[:]:
        pynet_logger.removeHandler(h)
    pynet_logger.addHandler(console_handler)
    
    class ForceNameFilter(logging.Filter):
        def filter(self, record):
            record.name = log_obj.name
            return True
            
    for f in console_handler.filters[:]:
        console_handler.removeFilter(f)
    console_handler.addFilter(ForceNameFilter())

def save_locally(ds, endpoint_dir, calling_aet, ep_conf):
    """Saves the DICOM file to the retry queue and logs the action."""
    refresh_logger(logger, get_conf_val(ep_conf, 'DEBUG', 'logname', 'Endpoint'), 
                   get_conf_val(ep_conf, 'DEBUG', 'loglevel', 'INFO'))
    
    storage_path = os.path.join(endpoint_dir, calling_aet)
    os.makedirs(storage_path, exist_ok=True)
    full_path = os.path.join(storage_path, f"{ds.SOPInstanceUID}.dcm")
    ds.save_as(full_path, enforce_file_format=True)
    logger.info(f"Queued locally: {full_path}")

def process_endpoint(ds, endpoint_path, calling_aet, router_lvl):
    """Coordinates filtering, scheduling, and forwarding for a specific endpoint."""

    ep_conf = get_config(os.path.join(endpoint_path, 'config.ini'))
    if not ep_conf: return
    
    ep_lvl = get_conf_val(ep_conf, 'DEBUG', 'loglevel', 'INFO')
    out_lvl = ep_lvl if getattr(logging, ep_lvl) < getattr(logging, router_lvl) else router_lvl
    
    target_aet = get_conf_val(ep_conf, 'ENDPOINT', 'ae_title', 'STORESCU')
    target_name = get_conf_val(ep_conf, 'DEBUG', 'logname', endpoint_path)
    refresh_logger(logger, f"Router->{target_name}", out_lvl)

    host = get_conf_val(ep_conf, 'ENDPOINT', 'hostname', 'localhost')
    port = int(get_conf_val(ep_conf, 'ENDPOINT', 'port', '5678'))
    
    # 1. Filtering Logic (SOP Class Whitelist/Blacklist)
    blacklist = [uid.strip() for uid in get_conf_val(ep_conf, 'ENDPOINT', 'blacklist', "").split(',') if uid.strip()]
    whitelist = [uid.strip() for uid in get_conf_val(ep_conf, 'ENDPOINT', 'whitelist', "").split(',') if uid.strip()]
    
    if whitelist and ds.SOPClassUID not in whitelist:
        logger.warning(f"SOP {ds.SOPClassUID} not whitelisted for {target_name}")
        return

    if ds.SOPClassUID in blacklist:
        logger.warning(f"SOP {ds.SOPClassUID} blacklisted for {target_name}")
        return

    # 2. Scheduling Logic
    schedule = get_conf_val(ep_conf, 'SCHEDULE', 'scheduled', "")
    if not is_within_schedule(schedule,logger):
        logger.info(f"Outside schedule for {target_name}. Saving to retry queue.")
        save_locally(ds, endpoint_path, calling_aet, ep_conf)
        return
        
    # 3. Transmission via common safe helper
    success, message = safe_dicom_send(ds, host, port, target_aet, calling_aet)
    
    if not success:
        # Distinguish between network drops and DICOM rejections in logs
        if "Network Error" in message:
            logger.error(f"Target {target_name} unreachable: {message}. Saving for retry.")
        else:
            logger.warning(f"Target {target_name} rejected transfer: {message}. Saving for retry.")
        
        save_locally(ds, endpoint_path, calling_aet, ep_conf)

def handle_store(event, working_dir):
    """C-STORE handler with smart configuration reloading."""
    global _CACHED_CONF, _CACHED_MTIME
    
    config_path = os.path.join(working_dir, 'config.ini')
    
    try:
        # 1. Check if modified
        current_mtime = os.path.getmtime(config_path)
        
        # 2. if modified read
        if current_mtime > _CACHED_MTIME:
            new_conf = get_config(config_path)
            if new_conf:
                _CACHED_CONF = new_conf
                _CACHED_MTIME = current_mtime
                logger.info("Configuration reloaded (file changed).")
            else:
                logger.warning("Config file changed but invalid. Using last known good config.")
    except Exception as e:
        # keep Cached if 
        if not _CACHED_CONF:
            # if cannot start
            logger.error(f"Critical: Configuration unreachable: {e}")
            return 0xA700 # Refused
        logger.debug(f"Config file unreachable, using cached version: {e}")

    main_conf = _CACHED_CONF
    
    # Logger refresh
    r_lvl = get_conf_val(main_conf, 'DEBUG', 'loglevel', 'INFO')
    r_name = get_conf_val(main_conf, 'DEBUG', 'logname', 'Router')
    refresh_logger(logger, r_name, r_lvl)
    
    ds = event.dataset
    ds.file_meta = event.file_meta
    
    logger.info(f"Incoming C-STORE from {event.assoc.requestor.ae_title}")

    # Router-level Global Filters
    router_whitelist = [uid.strip() for uid in get_conf_val(main_conf, 'DICOM', 'whitelist', "").split(',') if uid.strip()]
    router_blacklist = [uid.strip() for uid in get_conf_val(main_conf, 'DICOM', 'blacklist', "").split(',') if uid.strip()]

    if router_whitelist and ds.SOPClassUID not in router_whitelist:
        logger.error(f"SOP {ds.SOPClassUID} rejected globally: NOT in Whitelist")
        return 0xA700 # Refused: Out of Resources (commonly used for filtering)

    if ds.SOPClassUID in router_blacklist:
        logger.error(f"SOP {ds.SOPClassUID} rejected globally: Blacklisted")
        return 0xA700

    # Process all endpoint subdirectories
    for entry in filter(lambda e: e.is_dir(), os.scandir(working_dir)):
        process_endpoint(ds, entry.path, event.assoc.requestor.ae_title, r_lvl)
        
    return 0x0000

def run_server(working_dir):
    """Starts the DICOM SCP server with mandatory initial configuration."""
    global _CACHED_CONF, _CACHED_MTIME
    
    config_path = os.path.join(working_dir, 'config.ini')
    
    # 1. Initial configuration load (Mandatory for startup)
    _CACHED_CONF = get_config(config_path)
    if not _CACHED_CONF:
        print(f"CRITICAL ERROR: Configuration file missing or invalid at {config_path}")
        sys.exit(1) # Exit process if no valid config is found at startup
        
    # Set initial mtime for the cache tracker
    _CACHED_MTIME = os.path.getmtime(config_path)

    # 2. Extract basic settings for server startup
    ae_title = get_conf_val(_CACHED_CONF, 'DICOM', 'ae_title', 'ROUTER_SCP')
    log_name = get_conf_val(_CACHED_CONF, 'DEBUG', 'logname', 'Router')
    log_lvl = get_conf_val(_CACHED_CONF, 'DEBUG', 'loglevel', 'INFO')
    
    # 3. Setup initial logger state
    refresh_logger(logger, log_name, log_lvl)
    
    # 4. Initialize Network Application Entity
    ae = AE(ae_title=ae_title)
    ae.supported_contexts = StoragePresentationContexts
    ae.add_supported_context('1.2.840.10008.1.1') # Verification (C-ECHO)
    
    # 5. Map DICOM events to handlers
    # working_dir is passed to handle_store for dynamic config checking
    handlers = [
        (evt.EVT_C_STORE, handle_store, [working_dir]), 
        (evt.EVT_C_ECHO, lambda x: 0x0000)
    ]
    
    logger.info(f"DICOM Router Engine Started (AET: {ae_title})")
    
    # 6. Start the SCP server (Blocking)
    ae.start_server(('0.0.0.0', 11112), block=True, evt_handlers=handlers)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--working-dir', type=str, default='/app/Working')
    args = parser.parse_args()
    
    run_server(os.path.abspath(args.working_dir))