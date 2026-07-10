import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime, timezone
from uuid import uuid4

def gen_id(prefix=''):
    return f'{prefix}{str(uuid4())[:8]}'

async def generate_bills():
    client = AsyncIOMotorClient(os.environ.get('MONGO_URL'))
    db = client[os.environ.get('DB_NAME', 'crm_db')]
    
    # Get all approved entries
    entries = await db.time_entries.find({'status': 'approved'}, {'_id': 0}).to_list(1000)
    
    if not entries:
        print('No approved entries found')
        return
    
    # Calculate date range
    dates = [e['date'] for e in entries if 'date' in e]
    if not dates:
        print('No dates found in entries')
        return
    
    period_start = min(dates)
    period_end = max(dates)
    
    print(f'Processing {len(entries)} approved entries from {period_start} to {period_end}')
    
    # Group by business/contractor
    grouped = {}
    for entry in entries:
        user = await db.users.find_one({'user_id': entry['user_id']}, {'_id': 0})
        if not user:
            continue
        
        business_id = user.get('contractor_business_id')
        key = business_id if business_id else entry['user_id']
        
        if key not in grouped:
            grouped[key] = {'business_id': business_id, 'user_id': entry['user_id'], 'entries': []}
        grouped[key]['entries'].append(entry)
    
    print(f'Grouped into {len(grouped)} groups')
    
    # Create bills
    bills_created = 0
    for key, group in grouped.items():
        # Check if bill exists
        query = {'period_start': period_start, 'period_end': period_end}
        if group['business_id']:
            query['contractor_business_id'] = group['business_id']
        else:
            query['contractor_user_id'] = group['user_id']
        
        existing = await db.ap_bills.find_one(query)
        if existing:
            print(f'  Bill already exists for {key}, skipping')
            continue
        
        # Get contractor info
        if group['business_id']:
            business = await db.contractor_businesses.find_one({'business_id': group['business_id']}, {'_id': 0})
            if not business:
                continue
            contractor_name = business['business_name']
            business_name = business['business_name']
            payment_terms = business.get('payment_terms', 'net_30')
        else:
            user = await db.users.find_one({'user_id': group['user_id']}, {'_id': 0})
            if not user:
                continue
            contractor_name = user['name']
            business_name = None
            payment_terms = 'net_30'
        
        # Create line items
        line_items = []
        time_entry_ids = []
        for entry in group['entries']:
            user = await db.users.find_one({'user_id': entry['user_id']}, {'_id': 0})
            project = None
            if entry.get('project_id'):
                project = await db.projects.find_one({'project_id': entry['project_id']}, {'_id': 0})
            contract = None
            if entry.get('contract_id'):
                contract = await db.contracts.find_one({'contract_id': entry['contract_id']}, {'_id': 0})
            
            hours = entry.get('duration_hours', entry.get('hours', 0))
            rate = entry.get('cost_rate', 0)
            
            line_items.append({
                'contractor_name': user['name'] if user else 'Unknown',
                'project_name': project['name'] if project else None,
                'contract_name': contract['name'] if contract else None,
                'hours': hours,
                'rate': rate,
                'amount': hours * rate,
                'time_entry_ids': [entry['time_entry_id']]
            })
            time_entry_ids.append(entry['time_entry_id'])
        
        total = sum(item['amount'] for item in line_items)
        
        # Get next bill number
        current_year = datetime.now(timezone.utc).year
        bills = await db.ap_bills.find({"bill_number": {"$regex": f"^AP-{current_year}-"}}).sort("bill_number", -1).limit(1).to_list(1)
        if bills:
            last_num = int(bills[0]['bill_number'].split('-')[-1])
            next_num = last_num + 1
        else:
            next_num = 1
        
        bill_number = f'AP-{current_year}-{next_num:03d}'
        
        # Create bill
        bill = {
            'bill_id': gen_id('bill_'),
            'bill_number': bill_number,
            'contractor_business_id': group['business_id'],
            'contractor_business_name': business_name,
            'contractor_user_id': None if group['business_id'] else group['user_id'],
            'contractor_name': contractor_name,
            'period_start': period_start,
            'period_end': period_end,
            'billing_frequency': 'weekly',
            'line_items': line_items,
            'time_entry_ids': time_entry_ids,
            'subtotal': total,
            'total': total,
            'status': 'pending_finance_review',
            'payment_terms': payment_terms,
            'payment_method': 'ACH',
            'notes': 'Auto-generated from approved time entries',
            'created_at': datetime.now(timezone.utc),
            'created_by': 'system'
        }
        
        await db.ap_bills.insert_one(bill)
        print(f'  ✓ Created {bill_number} for {contractor_name}: ${total:,.2f}')
        bills_created += 1
    
    print(f'\n✅ Created {bills_created} AP bills')
    client.close()

if __name__ == '__main__':
    asyncio.run(generate_bills())
