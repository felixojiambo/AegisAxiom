# Backend Archive

This directory contains deprecated scripts and old code that is no longer used but kept for reference.

## 📂 Contents

### `/old_scripts/`
Old data generation and utility scripts that have been replaced:

- `generate_test_data.py` - Original test data script (v1)
- `generate_test_data_v2.py` - Second iteration (v2)
- `generate_comprehensive_test_data.py` - Attempted comprehensive version
- `manual_bill_generation.py` - Old manual bill creation script

**Replaced by:** `/app/scripts/seeds/seed_test_data.py`

## ⚠️ Important

**DO NOT USE** these scripts. They are:
- Outdated
- May have bugs or incomplete data
- Not maintained
- Kept only for historical reference

## 🔄 Migration Notes

The current seed script (`/app/scripts/seeds/seed_test_data.py`) includes:
- All features from old scripts
- Better data structure
- Complete relationships
- Proper cleanup capability
- More realistic test scenarios

## 📝 History

1. **v1 (`generate_test_data.py`):** Basic user and account seeding
2. **v2 (`generate_test_data_v2.py`):** Added contracts and projects
3. **Comprehensive:** Attempted to add all entities but incomplete
4. **Manual Bill:** Special script just for bill generation
5. **Current (`seed_test_data.py`):** Complete rewrite with all features
