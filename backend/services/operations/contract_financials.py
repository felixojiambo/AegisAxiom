"""Operations contract financial calculations.

Canonical location for contract financial logic, lifted here from the legacy
``backend/services/contract_financials.py`` as part of the operations namespace
spinout (Wave 2G / Wave 3A cleanup). The legacy module has been deleted; all
importers should reference this path.
"""
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta


def calculate_contract_financials(contract: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate AR, AP, and Margin summaries for a contract.

    Returns:
        Dict with ar_summary, ap_summary, margin_summary
    """
    # Calculate AR (what customer owes us)
    ar_summary = calculate_ar_summary(contract)

    # Calculate AP (what we owe contractors)
    ap_summary = calculate_ap_summary(contract)

    # Calculate Margin (profitability)
    margin_summary = calculate_margin_summary(ar_summary, ap_summary)

    return {
        'ar_summary': ar_summary,
        'ap_summary': ap_summary,
        'margin_summary': margin_summary
    }


def calculate_ar_summary(contract: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate Accounts Receivable (what customer owes us).

    Based on contract type:
    - BOH: total_hours * hourly_rate (or sum of resource_types)
    - BOA: Estimated based on resource sell rates
    - Fixed Fee: total_amount
    - Monthly: monthly_rate * contract duration in months
    """
    contract_type = contract.get('contract_type', 'boh')

    if contract_type == 'boh':
        # Block of Hours: calculate from resource_types or fallback to total_hours
        resource_types = contract.get('resource_types', [])
        if resource_types:
            # Sum up all resource types: hours * sell_rate
            total_ar = sum(
                float(rt.get('hours_purchased', 0)) * float(rt.get('sell_rate', 0))
                for rt in resource_types
            )
        else:
            # Fallback: total_hours * hourly_rate
            total_ar = float(contract.get('total_hours', 0)) * float(contract.get('hourly_rate', 0))

        hours_consumed = float(contract.get('hours_consumed', 0))
        total_hours = float(contract.get('total_hours', 0)) or sum(
            float(rt.get('hours_purchased', 0)) for rt in resource_types
        )

        # Calculate based on consumption
        if total_hours > 0:
            invoiced_to_date = (hours_consumed / total_hours) * total_ar
            outstanding = total_ar - invoiced_to_date
            percent_consumed = (hours_consumed / total_hours) * 100
        else:
            invoiced_to_date = 0
            outstanding = total_ar
            percent_consumed = 0

        return {
            'total_contract_value': round(total_ar, 2),
            'invoiced_to_date': round(invoiced_to_date, 2),
            'outstanding': round(outstanding, 2),
            'hours_purchased': round(total_hours, 2),
            'hours_consumed': round(hours_consumed, 2),
            'hours_remaining': round(total_hours - hours_consumed, 2),
            'percent_consumed': round(percent_consumed, 1),
            'forecast_monthly': 0  # TODO: Calculate based on historical consumption
        }

    elif contract_type == 'boa':
        # Bill on Actuals: estimate based on resource sell rates
        resources = contract.get('resources', [])
        resource_types = contract.get('resource_types', [])

        # Estimate AR from resource types (sell rates)
        if resource_types:
            estimated_ar = sum(
                float(rt.get('hours_purchased', 0)) * float(rt.get('sell_rate', 0))
                for rt in resource_types
            )
        elif resources:
            # Fallback: use resources with sell_rate if available
            estimated_ar = sum(
                float(r.get('hours_allocated', 0)) * float(r.get('sell_rate', 0))
                for r in resources
            )
        else:
            estimated_ar = 0

        # BOA typically bills periodically, so this is an estimate
        return {
            'total_contract_value': round(estimated_ar, 2),
            'invoiced_to_date': 0,  # TODO: Sum of actual invoices
            'outstanding': round(estimated_ar, 2),
            'estimated': True,
            'note': 'BOA contracts bill on actual hours worked',
            'forecast_monthly': 0  # TODO: Calculate based on billing_period
        }

    elif contract_type == 'fixed_fee':
        # Fixed Fee: use total_amount and milestones
        total_amount = float(contract.get('total_amount', 0))
        milestones = contract.get('milestones', [])

        # Calculate invoiced based on completed milestones
        invoiced = sum(
            float(m.get('amount', 0))
            for m in milestones
            if m.get('status') in ['completed', 'paid']
        )

        return {
            'total_contract_value': round(total_amount, 2),
            'invoiced_to_date': round(invoiced, 2),
            'outstanding': round(total_amount - invoiced, 2),
            'milestones_total': len(milestones),
            'milestones_completed': len([m for m in milestones if m.get('status') in ['completed', 'paid']]),
            'forecast_monthly': 0  # TODO: Project milestone completion rate
        }

    elif contract_type == 'monthly':
        # Monthly Recurring: calculate based on start/end dates
        monthly_rate = float(contract.get('monthly_rate', 0))
        start_date_str = contract.get('start_date')
        end_date_str = contract.get('end_date')

        # Calculate contract duration
        if start_date_str:
            start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
            if end_date_str:
                end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            else:
                # If no end date, project 12 months forward
                end_date = start_date + relativedelta(months=12)

            # Calculate months elapsed and total
            now = datetime.now(timezone.utc)
            months_elapsed = max(0, (now.year - start_date.year) * 12 + now.month - start_date.month)
            total_months = max(1, (end_date.year - start_date.year) * 12 + end_date.month - start_date.month)

            invoiced = months_elapsed * monthly_rate
            total_ar = total_months * monthly_rate

            return {
                'total_contract_value': round(total_ar, 2),
                'invoiced_to_date': round(invoiced, 2),
                'outstanding': round(total_ar - invoiced, 2),
                'monthly_rate': round(monthly_rate, 2),
                'months_elapsed': months_elapsed,
                'months_total': total_months,
                'forecast_monthly': round(monthly_rate, 2)
            }
        else:
            return {
                'total_contract_value': 0,
                'invoiced_to_date': 0,
                'outstanding': 0,
                'monthly_rate': round(monthly_rate, 2),
                'note': 'Start date required for calculation',
                'forecast_monthly': round(monthly_rate, 2)
            }

    else:
        # Unknown type - return zeros
        return {
            'total_contract_value': 0,
            'invoiced_to_date': 0,
            'outstanding': 0,
            'note': f'Unknown contract type: {contract_type}'
        }


def calculate_ap_summary(contract: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate Accounts Payable (what we owe contractors).

    Based on resources assigned to contract with their cost rates.
    """
    resources = contract.get('resources', [])
    resource_types = contract.get('resource_types', [])

    # Calculate total AP from resources (actual costs)
    if resources:
        # Build a map of resource_type_id -> hours_purchased from resource_types
        rt_hours_map = {rt.get('id', ''): float(rt.get('hours_purchased', 0)) for rt in resource_types}

        total_cost = 0
        resource_breakdown = []
        for r in resources:
            cost_rate = float(r.get('cost_rate', 0))
            # Use hours_allocated if set, otherwise estimate from linked resource type
            hours = float(r.get('hours_allocated', 0))
            if hours == 0 and r.get('resource_type_id'):
                hours = rt_hours_map.get(r['resource_type_id'], 0)

            cost = hours * cost_rate
            total_cost += cost
            resource_breakdown.append({
                'resource_name': r.get('name', 'Unknown'),
                'role': r.get('role', ''),
                'hours_allocated': hours,
                'cost_rate': cost_rate,
                'total_cost': round(cost, 2)
            })

        return {
            'total_cost_estimate': round(total_cost, 2),
            'billed_to_date': 0,  # TODO: Sum of actual AP bills/expenses
            'outstanding': round(total_cost, 2),
            'resources_assigned': len(resources),
            'resource_breakdown': resource_breakdown,
            'forecast_monthly': 0  # TODO: Calculate based on time tracking
        }

    elif resource_types:
        # Estimate from resource types — cost_rate is on resources not resource_types,
        # so we can only estimate AP if resource_types have a cost_rate field (e.g., from seeded data)
        total_cost_estimate = sum(
            float(rt.get('hours_purchased', 0)) * float(rt.get('cost_rate', 0))
            for rt in resource_types
        )

        return {
            'total_cost_estimate': round(total_cost_estimate, 2),
            'billed_to_date': 0,
            'outstanding': round(total_cost_estimate, 2),
            'resources_assigned': 0,
            'note': 'Estimated from resource types (no actual resources assigned)',
            'forecast_monthly': 0
        }

    else:
        # No resources = no AP estimate
        return {
            'total_cost_estimate': 0,
            'billed_to_date': 0,
            'outstanding': 0,
            'resources_assigned': 0,
            'note': 'No resources assigned - unable to estimate costs',
            'forecast_monthly': 0
        }


def calculate_margin_summary(ar_summary: Dict[str, Any], ap_summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate profitability metrics (Margin = AR - AP).
    """
    total_ar = ar_summary.get('total_contract_value', 0)
    total_ap = ap_summary.get('total_cost_estimate', 0)

    gross_margin_dollars = total_ar - total_ap

    if total_ar > 0:
        gross_margin_percent = (gross_margin_dollars / total_ar) * 100
    else:
        gross_margin_percent = 0

    # Determine margin health
    if gross_margin_percent >= 40:
        margin_health = 'excellent'
        health_color = 'green'
    elif gross_margin_percent >= 25:
        margin_health = 'good'
        health_color = 'green'
    elif gross_margin_percent >= 15:
        margin_health = 'fair'
        health_color = 'yellow'
    elif gross_margin_percent >= 0:
        margin_health = 'low'
        health_color = 'orange'
    else:
        margin_health = 'negative'
        health_color = 'red'

    return {
        'gross_margin_dollars': round(gross_margin_dollars, 2),
        'gross_margin_percent': round(gross_margin_percent, 1),
        'margin_health': margin_health,
        'health_color': health_color,
        'total_ar': round(total_ar, 2),
        'total_ap': round(total_ap, 2),
        # Future: current_margin (based on actual invoiced vs billed)
        # Future: projected_margin (based on forecasts)
    }


def get_margin_health_display(margin_percent: float):
    """
    Helper to get margin health label and color.

    Returns:
        tuple: (health_label, color_code)
    """
    if margin_percent >= 40:
        return ('Excellent', 'green')
    elif margin_percent >= 25:
        return ('Good', 'green')
    elif margin_percent >= 15:
        return ('Fair', 'yellow')
    elif margin_percent >= 0:
        return ('Low', 'orange')
    else:
        return ('Negative', 'red')
