use std::collections::BTreeSet;
use std::error::Error;

use toml::Value;

const MANIFEST: &str = include_str!("../Cargo.toml");

#[test]
fn production_dependencies_preserve_the_pure_routing_boundary() -> Result<(), Box<dyn Error>> {
    let manifest = toml::from_str::<Value>(MANIFEST)?;
    let dependencies = manifest
        .get("dependencies")
        .and_then(Value::as_table)
        .ok_or("routing-core manifest must declare dependencies")?;
    let actual = dependencies
        .keys()
        .map(String::as_str)
        .collect::<BTreeSet<_>>();
    let expected = ["regex", "serde", "thiserror", "wayfinder-runtime-contracts"]
        .into_iter()
        .collect::<BTreeSet<_>>();

    assert_eq!(
        actual, expected,
        "adding a production dependency requires an explicit routing-boundary review"
    );
    Ok(())
}
