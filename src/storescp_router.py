import os
import sys
import configparser
import logging
import argparse
from pynetdicom import AE, evt, StoragePresentationContexts

# --- GLOBAL LOGGING INITIALIZATION ---

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
formatter = logging.Formatter(LOG_FORMAT)

# A TE SAJÁT HANDLERED (ez az, ami ténylegesen kiír a képernyőre)
# Ezt globálissá tesszük, hogy bárhova hozzáadhassuk
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)

# Router logger setup
logger = logging.getLogger("Router")
logger.addHandler(console_handler)
logger.propagate = False 

def refresh_logger(log_obj, name, level_str):
    """
    Forces all pynetdicom messages to use the Router's handler and name.
    """
    level = getattr(logging, level_str.upper(), logging.INFO)
    
    # 1. Update the Router logger
    log_obj.name = name
    log_obj.setLevel(level)
    console_handler.setLevel(level)

    # 2. SEIZE CONTROL of pynetdicom
    pynet_logger = logging.getLogger('pynetdicom')
    pynet_logger.setLevel(level)
    
    # Eltávolítunk minden régi handlert, hogy ne legyen káosz
    for h in pynet_logger.handlers[:]:
        pynet_logger.removeHandler(h)
        
    # KÖZVETLENÜL hozzáadjuk a te console_handleredet a pynetdicom-hoz
    # Így nem kell a propagate-re várni, azonnal a te formátumoddal ír ki
    pynet_logger.addHandler(console_handler)
    
    # Ha azt akarod, hogy a logban "Router" névvel szerepeljenek a pynetdicom üzenetei is,
    # akkor egy Filtert kell a handlerre tenni (opcionális):
    class ForceNameFilter(logging.Filter):
        def filter(self, record):
            record.name = log_obj.name # Átveszi az aktuális Router/Endpoint nevet
            return True
            
    # Töröljük a régi filtereket és adjuk hozzá az újat
    for f in console_handler.filters[:]:
        console_handler.removeFilter(f)
    console_handler.addFilter(ForceNameFilter())

def load_config(path):
    """Loads INI file and returns ConfigParser object."""
    if not os.path.exists(path):
        return None
    config = configparser.ConfigParser()
    try:
        config.read(path, encoding='utf-8')
    except Exception:
        return None
    return config

def get_conf_val(conf, section, key, default=""):
    """Helper to safely retrieve configuration values."""
    if not conf or not conf.has_section(section):
        return default
    return conf.get(section, key, fallback=default)

def try_send_c_store(ds, host, port, target_aet, calling_aet):
    """Attempts to forward the DICOM dataset while preserving the transfer syntax."""
    ae = AE(ae_title=calling_aet)
    # Ensure the original transfer syntax is supported in the association
    ae.add_requested_context(ds.SOPClassUID, ds.file_meta.TransferSyntaxUID)
    
    assoc = ae.associate(host, port, ae_title=target_aet)
    if assoc.is_established:
        status = assoc.send_c_store(ds)
        assoc.release()
        return status and status.Status == 0x0000
    return False

def save_locally(ds, endpoint_dir, calling_aet, ep_conf):
    """Saves the DICOM file locally and reconfigures logger for the specific endpoint."""
    refresh_logger(logger, get_conf_val(ep_conf, 'DEBUG', 'logname', 'Endpoint'), 
                   get_conf_val(ep_conf, 'DEBUG', 'loglevel', 'INFO'))
    
    storage_path = os.path.join(endpoint_dir, calling_aet)
    os.makedirs(storage_path, exist_ok=True)
    full_path = os.path.join(storage_path, f"{ds.SOPInstanceUID}.dcm")
    ds.save_as(full_path, enforce_file_format=True)
    logger.info(f"Stored locally: {full_path}")

def process_endpoint(ds, endpoint_path, calling_aet, router_lvl):
    """Coordinates forwarding with robust error handling for network failures."""
    ep_conf = load_config(os.path.join(endpoint_path, 'config.ini'))
    if not ep_conf: return
    
    ep_lvl = get_conf_val(ep_conf, 'DEBUG', 'loglevel', 'INFO')
    # Use the more verbose level between the global router and the specific endpoint
    out_lvl = ep_lvl if getattr(logging, ep_lvl) < getattr(logging, router_lvl) else router_lvl
    
    target_aet = get_conf_val(ep_conf, 'ENDPOINT', 'ae_title', 'STORESCU')
    refresh_logger(logger, f"Router->{target_aet}", out_lvl)

    host = get_conf_val(ep_conf, 'ENDPOINT', 'hostname', 'localhost')
    port = int(get_conf_val(ep_conf, 'ENDPOINT', 'port', '5678'))
    blacklist = [uid.strip() for uid in get_conf_val(ep_conf, 'ENDPOINT', 'blacklist', "").split(',') if uid.strip()]

    if ds.SOPClassUID in blacklist:
        logger.warning(f"SOP {ds.SOPClassUID} blacklisted for {target_aet}")
        return
        
    try:
        #try to send
        success = try_send_c_store(ds, host, port, target_aet, calling_aet)
        
        if not success:
            logger.warning(f"Target {target_aet} returned failure status. Saving locally.")
            save_locally(ds, endpoint_path, calling_aet, ep_conf)
            
    except Exception as e:
        # Networking problems (host unreachable, DNS problems )
        logger.error(f"Network error: Could not connect to {host} ({e}).")
        logger.info(f"Target is likely down. Saving instance to retry queue.")
        save_locally(ds, endpoint_path, calling_aet, ep_conf)

def handle_store(event, working_dir):
    """C-STORE handler with dynamic configuration reloading."""
    main_conf = load_config(os.path.join(working_dir, 'config.ini'))
    r_lvl = get_conf_val(main_conf, 'DEBUG', 'loglevel', 'INFO')
    r_name = get_conf_val(main_conf, 'DEBUG', 'logname', 'Router')
    refresh_logger(logger, r_name, r_lvl)
    
    ds = event.dataset
    ds.file_meta = event.file_meta
    
    logger.info(f"Incoming C-STORE from {event.assoc.requestor.ae_title}")

    # Router-level blacklist check
    router_blacklist = [uid.strip() for uid in get_conf_val(main_conf, 'DICOM', 'blacklist', "").split(',') if uid.strip()]
    if ds.SOPClassUID in router_blacklist:
        logger.error(f"SOP {ds.SOPClassUID} rejected by Router blacklist")
        return 0xB000

    # Iterate through all configured endpoints (subdirectories)
    for entry in filter(lambda e: e.is_dir(), os.scandir(working_dir)):
        process_endpoint(ds, entry.path, event.assoc.requestor.ae_title, r_lvl)
        
    return 0x0000

def run_server(working_dir):
    """Starts the DICOM server with initial configuration."""
    main_conf = load_config(os.path.join(working_dir, 'config.ini'))
    ae_title = get_conf_val(main_conf, 'DICOM', 'ae_title', 'ROUTER_SCP')
    
    # Initial logger setup
    refresh_logger(logger, get_conf_val(main_conf, 'DEBUG', 'logname', 'Router'), 
                   get_conf_val(main_conf, 'DEBUG', 'loglevel', 'INFO'))
    
    ae = AE(ae_title=ae_title)
    ae.supported_contexts = StoragePresentationContexts
    ae.add_supported_context('1.2.840.10008.1.1') # Verification (C-ECHO)
    
    handlers = [
        (evt.EVT_C_STORE, handle_store, [working_dir]), 
        (evt.EVT_C_ECHO, lambda x: 0x0000)
    ]
    
    logger.info(f"DICOM Router Engine Started (AET: {ae_title})")
    ae.start_server(('0.0.0.0', 11112), block=True, evt_handlers=handlers)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--working-dir', type=str, default='/app/Working')
    args = parser.parse_args()
    
    # Ensure absolute path for consistency
    work_dir = os.path.abspath(args.working_dir)
    run_server(work_dir)