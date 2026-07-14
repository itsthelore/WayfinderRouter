fn main() {
    println!("cargo:rerun-if-changed=src/xpc_bridge.m");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        cc::Build::new()
            .file("src/xpc_bridge.m")
            .flag("-fobjc-arc")
            .flag("-fblocks")
            .compile("wayfinder_apple_foundation_xpc_bridge");
        println!("cargo:rustc-link-lib=framework=Foundation");
    }
}
